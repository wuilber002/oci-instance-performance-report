#!/bin/env python

try:
    import oci
except ImportError as error:
    print(error)
    print("")
    print("OCI libraries not installed. Please install them with 'pip3 install oci'")
    exit(-1)

import re
import os
import sys
import csv
import json
import matplotlib.pyplot as plt
from fpdf import FPDF
from genericpath import exists
from zipfile import ZIP_DEFLATED, ZipFile
from datetime import datetime, timedelta, timezone

from oci.core.models import volume_attachment

if len(sys.argv) >= 2:
    if not re.match('^principal$', str(sys.argv[1]).lower()):
        config_file = sys.argv[1]
        if not os.path.isfile(config_file):
            print('[ERRO] OCI config file not exist (%s).' % (config_file))
            sys.exit()
        oci_config = oci.config.from_file(config_file, 'DEFAULT')
    else:
        oci_config = {}

        # By default this will hit the auth service in the region returned by
        # http://169.254.169.254/opc/v2/instance/region on the instance.
        signer = oci.auth.signers.InstancePrincipalsSecurityTokenSigner()

    if len(sys.argv) >= 3:
        compartment_ocid = sys.argv[2]
        print('\n   !!! Executando a pesquisa de instances apenas no compartment !!!')
        print('   !!!      informado e em todas as regioes subscritas...       !!!\n\n')
    else:
        compartment_ocid = oci_config['tenancy']

else:
    print('[ERRO]: missing oci config file')
    sys.exit(1)

# -----------------------------------------------------------------------------
def get_compartments(root_compartment_ocid):
    """
    Lista todos os compartments de forma recursiva
    apartir do compartment_ocid informado.
    """
    # -------------------------------------------------------------------------
    # Consulta o nome do compartment_ocid recebido:
    root_compartment_name = (identity_client.get_compartment(root_compartment_ocid).data.name).strip()
    # -------------------------------------------------------------------------
    # Objeto de retorno com a lista de todos comparments encontrados:
    compartments = [{
        'name': root_compartment_name,
        'id': root_compartment_ocid,
        'lifecycle_state': identity_client.get_compartment(root_compartment_ocid).data.lifecycle_state
    }]
    # -------------------------------------------------------------------------
    # Executa a pesquisa recursiva pelos compartments:
    for compartment in oci.pagination.list_call_get_all_results(
        identity_client.list_compartments,
        root_compartment_ocid
    ).data:
        if compartment.lifecycle_state == "ACTIVE":
            compartments.append({
                'name': ('%s/%s' % (root_compartment_name, (compartment.name).strip())),
                'id': compartment.id,
                'lifecycle_state': compartment.lifecycle_state
            })
            sub_compartment = get_compartments(compartment.id)
            if len(sub_compartment) > 1:
                for sub in sub_compartment:
                    if compartment.id != sub['id']:
                        compartments.append({
                            'name': ('%s/%s' % (root_compartment_name, sub['name'])),
                            'id': sub['id'],
                            'lifecycle_state': sub['lifecycle_state']
                        })
    return compartments

# -----------------------------------------------------------------------------
def get_instance(compute_client, compartment_id):
    """
    Lista todas as instances de dentro do compartment_ocid
    informado.
    """
    return oci.pagination.list_call_get_all_results(
        compute_client.list_instances,
        compartment_id
    ).data

# -----------------------------------------------------------------------------
def plotGraph(path, file, metrics):
    """
    Cria um grafico com os dados recebidos.
    """
    fontLegend = {'family': 'serif', 'color': 'black', 'size': 14}
    with open('.graphs', 'r', encoding='utf-8') as graphsFile:
        for line in graphsFile.readlines():
            if not re.match('^#', line):
                (graphs, legend_y) = line.split('~')
                plot_grapth = False
                for graph in (graphs.split(',')):
                    (metric_name, color) = graph.split(':')
                    if metric_name in metrics:
                        plot_grapth = True
                        plt.rcParams['figure.figsize'] = [9, 2.5]
                        plt.plot(
                            metrics[metric_name]['values']['x'],
                            metrics[metric_name]['values']['y'],
                            color=color,
                            linestyle='solid',
                            linewidth=1,
                            label=('%s | mim:%.2f, avg:%.2f, max:%.2f' % (
                                metric_name,
                                metrics[metric_name]['min'],
                                metrics[metric_name]['avg'],
                                metrics[metric_name]['max'])
                            )
                        )

                if plot_grapth:
                    plt.legend(bbox_to_anchor=(
                        0, 1.02, 1, 0.2), loc="lower left", mode="expand", borderaxespad=0, ncol=3)

                    plt.ylabel(legend_y, fontdict=fontLegend)
                    plt.xlabel("Timeaxis (day)", fontdict=fontLegend)
                    # plt.ylim(0, 100)
                    plt.savefig(
                        fname=(('%s/%s_%s.png') % (path, file, metric_name)),
                        dpi=100,
                        bbox_inches='tight',
                        pad_inches=0.1,
                        transparent=False
                    )
                    plt.close()
    f.close

# -----------------------------------------------------------------------------
def getMetrics(monitoring_client, query, namespace, compartment):
    """
    Recupera a lista de metricas da instancia.
    """
    summarize_metrics_data_response = monitoring_client.summarize_metrics_data(
        compartment_id=compartment,
        summarize_metrics_data_details=oci.monitoring.models.SummarizeMetricsDataDetails(
            namespace=namespace,
            query=query,
            start_time=start_time,
            end_time=end_time
        )
    )

    avg = min = max = False
    values = {"x": list(), "y": list()}
    if len(summarize_metrics_data_response.data) > 0:
        sum = 0
        for data in (summarize_metrics_data_response.data[0]).aggregated_datapoints:
            if re.match('NetworksBytes', query):
                data.value = (data.value/(1024*1024))

            values["x"].append(data.timestamp)
            values["y"].append(data.value)
            sum += data.value

            if max == False:
                max = data.value
            elif data.value > max:
                max = data.value

            if min == False:
                min = data.value
            elif data.value < min:
                min = data.value
        avg = (
            sum/len((summarize_metrics_data_response.data[0]).aggregated_datapoints))

    return[min, avg, max, values]

# -----------------------------------------------------------------------------
def zipdir(path, ziph):
    """
    Compacta os arquivos csv e pdf do relatorio
    """
    # ziph is zipfile handle
    for root, dirs, files in os.walk(path):
        for file in files:
            ziph.write(
                os.path.join(root, file),
                os.path.relpath(
                    os.path.join(root, file),
                    os.path.join(path, '.')
                )
            )

# -----------------------------------------------------------------------------
class PDF(FPDF):
    """
    Definicao customizada para customizar o header e footer
    do arquivo PDF de relatorio.
    """

    def header(self):
        # Do not print footer on first page
        if self.page_no() != 1:
            # Logo Oracle Cloud
            self.image(
                name='.oracle_cloud.png',
                x=2,  # posicao absoluta no eixo X
                y=2,  # posicao absoluta no eixo Y
                w=40,  # Largura
                h=14  # Altura
            )
            self.set_text_color(r=200, g=-1, b=-1)  # black
            self.set_font('Arial', 'B', 10)  # Arial bold 10
            self.set_y(3)  # Move from top
            # Title
            if self.cur_orientation == 'P':
                width = 189
            else:
                width = 265
            self.cell(
                h=5,       # Altura
                ln=0,
                w=width,   # Largura
                border=0,
                align='C',  # Alinhamento centralizado
                txt='Instance Performance Report'
            )
            # Line break
            self.ln(20)

    # Page footer
    def footer(self):
        # Do not print footer on first page
        if self.page_no() != 1:
            self.set_y(-10)  # Position at 1 cm from bottom
            self.set_font('Arial', 'I', 7)  # Arial Italic 7
            self.set_text_color(r=200, g=-1, b=-1)  # black
            self.cell(0, 10, 'Page ' + str(self.page_no()) +
                      '/{nb}', 0, 0, 'C')

# Bustable base line list:
burstable = {
    'BASELINE_1_8': "12.5%",
    'BASELINE_1_2': "50%",
    'BASELINE_1_1': "none"
}

# https://docs.oracle.com/en-us/iaas/Content/Monitoring/Tasks/buildingqueries.htm#MQLEdit
# +-------------+--------------------+
# | aggregation | Max range returned |
# |     1m      |     7  (days)      |
# |     5m      |     30 (days)      |
# |     1h      |     90 (days)      |
# |     1d      |     90 (days)      |
# +-------------+--------------------+
time_range = 1 # Tempo em dias. Valores possiveis entre 1-90
aggregation = '5m' # Valores possiveis: 1m, 5m, 1h, 1d


# -----------------------------------------------------------------------------
# lista de cores para output do script:
color = {
    'yellow': '\033[33m',
    'green': '\033[32m',
    'blue': '\033[34m',
    'red': '\033[31m',
    'clean': '\033[0m'
}

# -----------------------------------------------------------------------------
# Intancia o Identity client :
if 'signer' in vars() or 'signer' in globals():
    identity_client = oci.identity.IdentityClient(signer=signer, config=oci_config, retry_strategy=oci.retry.DEFAULT_RETRY_STRATEGY)
else:
    identity_client = oci.identity.IdentityClient(config=oci_config, retry_strategy=oci.retry.DEFAULT_RETRY_STRATEGY)

# -----------------------------------------------------------------------------
# Lista todas as regioes nas quais o tanancy esta subscrito:
regions = identity_client.list_region_subscriptions(oci_config['tenancy']).data

# -----------------------------------------------------------------------------
# Consulta o nome do tenancy
tenancy_name = (identity_client.get_tenancy(oci_config['tenancy']).data.name).strip()

# -----------------------------------------------------------------------------
# Diretorio para gravacao temporaria das imagens:
work_dir = 'work_dir'
file_path = ('./%s/%s' % (work_dir, tenancy_name))
if not exists(file_path):
    os.makedirs(file_path)
else:
    for i in os.listdir(file_path):
        os.remove(os.path.join(file_path, i))

# -----------------------------------------------------------------------------
# Diretorio para gravacao dos arquivos zip de report:
report_dir = './reports'
if not exists(report_dir):
    os.makedirs(report_dir)


# -----------------------------------------------------------------------------
# Monta o nome do arquivo de output com a lista de instances encontrdas:
today = datetime.now()
instance_list_file = ('%s/%s_instance_list_%s.csv' % (file_path, tenancy_name, today.strftime("%Y-%m-%d_%H-%M-%S")))
instance_perfornace_file = ('%s/%s_instance_perfornace_data_%s-%s_days.csv' % (file_path, tenancy_name, today.strftime("%Y-%m-%d_%H-%M-%S"), time_range))
instance_perfornace_report = ('%s/%s_instance_perfornace_data_%s-%s_days.pdf' % (file_path, tenancy_name, today.strftime("%Y-%m-%d_%H-%M-%S"), time_range))
zip_output_file = ('./%s/%s_%s_performance_report-%s_days.zip' % (report_dir, today.strftime("%Y-%m-%d_%H-%M-%S"), tenancy_name, time_range))

# -----------------------------------------------------------------------------
# Range de tempo para a coleta de dados de performance com o cliente de
# monitoracao:
today_utc = datetime.now(timezone.utc)
start_time = datetime.strptime((today_utc-timedelta(days=time_range)).strftime("%Y-%m-%dT%H:%M:%S.%fZ"), "%Y-%m-%dT%H:%M:%S.%fZ")
end_time = datetime.strptime(today_utc.strftime("%Y-%m-%dT%H:%M:%S.%fZ"), "%Y-%m-%dT%H:%M:%S.%fZ")

# -----------------------------------------------------------------------------
# Grava o header nos arquivos csv:
with open(instance_list_file, 'w', encoding='utf-8') as f:
    f.write('compartment,instance_name,os_name,os_version,region,lifecycle_state,shape,burstable,preemptible,reservation,dedicated_host,processor_description,ocpus,memory_in_gbs,boot_image_name,boot_size,boot_vpu,block_count,block_size,block_vpu_sum,age(days),ocid\n')
f.close

# -----------------------------------------------------------------------------
# Inicia a varedura do tenancy vasculhando dentro de cada compartment em todas
# as regions que o tenancy esta subscrito:
compartment_path = dict()
header_csv_perf_report = True

region_count = 0
region_count_total = len(regions)
for region_name in [str(es.region_name) for es in regions]:
    region_count += 1
    print('> [%02d/%02d] %s%s%s' % (region_count, region_count_total, color['blue'], region_name, color['clean']))
    oci_config['region'] = region_name

    # -------------------------------------------------------------------------
    # Intancia o Compute client :
    if 'signer' in vars() or 'signer' in globals():
        compute_client = oci.core.ComputeClient(signer=signer, config=oci_config, retry_strategy=oci.retry.DEFAULT_RETRY_STRATEGY)
        monitoring_client = oci.monitoring.MonitoringClient(signer=signer, config=oci_config, retry_strategy=oci.retry.DEFAULT_RETRY_STRATEGY)
        blockStorage_client = oci.core.BlockstorageClient(signer=signer, config=oci_config, retry_strategy=oci.retry.DEFAULT_RETRY_STRATEGY)
        identity_client = oci.identity.IdentityClient(signer=signer, config=oci_config, retry_strategy=oci.retry.DEFAULT_RETRY_STRATEGY)
    else:
        compute_client = oci.core.ComputeClient(config=oci_config, retry_strategy=oci.retry.DEFAULT_RETRY_STRATEGY)
        monitoring_client = oci.monitoring.MonitoringClient(config=oci_config, retry_strategy=oci.retry.DEFAULT_RETRY_STRATEGY)
        blockStorage_client = oci.core.BlockstorageClient(config=oci_config, retry_strategy=oci.retry.DEFAULT_RETRY_STRATEGY)
        identity_client = oci.identity.IdentityClient(config=oci_config, retry_strategy=oci.retry.DEFAULT_RETRY_STRATEGY)

    # -------------------------------------------------------------------------
    # Inicia o processamento analisando cada compartment do tenancy:
    volumeAttachmentList = {'boot': dict(), 'block': dict()}
    print('  + Making data cache +')
    for compartment in get_compartments(compartment_ocid):

        # Cria uma lista de boot/block volumes attachments para
        # consulta posterior e identificar o volume associado
        # a cada instance:
        for availability_domain in identity_client.list_availability_domains(compartment_id=compartment['id']).data:
            # Boot volume:
            for attachment in oci.pagination.list_call_get_all_results(
                compute_client.list_boot_volume_attachments,
                availability_domain=availability_domain.name,
                compartment_id=compartment['id']
            ).data:
                if not attachment.instance_id in volumeAttachmentList['boot']:
                    volumeAttachmentList['boot'][attachment.instance_id] = list(
                    )
                volumeAttachmentList['boot'][attachment.instance_id] = {
                    'id': attachment.boot_volume_id,
                    'compartment_id': attachment.compartment_id,
                    'availability_domain': attachment.availability_domain,
                    'lifecycle_state': attachment.lifecycle_state
                }

            # Block volume:
            for attachment in compute_client.list_volume_attachments(
                    availability_domain=availability_domain.name,
                    compartment_id=compartment['id']
            ).data:
                if not attachment.instance_id in volumeAttachmentList['block']:
                    volumeAttachmentList['block'][attachment.instance_id] = list(
                    )
                volumeAttachmentList['block'][attachment.instance_id].append(attachment)

    if len(volumeAttachmentList['boot']) == 0:
        print('  `-> No instances found! %s¯\_(%s⊙%s︿%s⊙%s)_/¯%s\n' % (color['yellow'], color['red'], color['green'], color['red'], color['yellow'], color['clean']))
        continue

    # -------------------------------------------------------------------------
    # Inicia o processamento analisando cada compartment do tenancy:
    for compartment in get_compartments(compartment_ocid):
        print('  - %s' % (compartment['name']))

        for instance in get_instance(compute_client, compartment['id']):
            compartment_path[instance.id] = (
                '[%s] %s' % (region_name, compartment['name']))
            time_created = datetime.strptime(
                str(instance.time_created).split(" ")[0], "%Y-%m-%d")

            # -----------------------------------------------------------------
            # Validacao do tipo da instance:
            instanceJson = json.loads(str(instance))
            instanceType = {
                'burstable': 'none',
                'preemptible': 'none',
                'dedicated_vm_host': 'none',
                'capacity_reservation': 'none'
            }
            if instanceJson['shape_config']['baseline_ocpu_utilization']:
                instanceType['burstable'] = burstable[instanceJson['shape_config']
                                                      ['baseline_ocpu_utilization']]
            if instanceJson['preemptible_instance_config']:
                instanceType['preemptible'] = 'yes'
            if instanceJson['capacity_reservation_id']:
                capacityReservationResponse = compute_client.get_compute_capacity_reservation(
                    capacity_reservation_id=instanceJson['capacity_reservation_id']
                ).data
                instanceType['capacity_reservation'] = (capacityReservationResponse.display_name).strip()
            if instanceJson['dedicated_vm_host_id']:
                dedicatedVmHostResponse = compute_client.get_dedicated_vm_host(
                    dedicated_vm_host_id=instanceJson['dedicated_vm_host_id']
                ).data
                instanceType['dedicated_vm_host'] = (dedicatedVmHostResponse.display_name).strip()

            # -----------------------------------------------------------------
            # Lista de informacoes para coleta dos volumes da instance
            # (boot e block)
            volumes = {
                'boot': {'size': 'null', 'vpu': 'null', 'image': '', 'os': {'name': '', 'version': ''}},
                'block': list()
            }
            # -------------------------------------------------------------
            # Procura por Boot Volume:
            if instance.id in volumeAttachmentList['boot']:
                bootVolumeResponse = blockStorage_client.get_boot_volume(
                    boot_volume_id=volumeAttachmentList['boot'][instance.id]['id']
                ).data
                
                volumes['boot']['image'] = (bootVolumeResponse.display_name).strip()
                volumes['boot']['size'] = bootVolumeResponse.size_in_gbs
                volumes['boot']['vpu'] = bootVolumeResponse.vpus_per_gb

                # --------------------------------------------------------
                # Verifica se o boot volume utilizado nao foi criado em
                # outra regiao e transferido para essa. nesses cassos
                # o boot volume preserva o ocid da imagem de origem,
                # sendo assim, necessario alterar a regiao
                # no client para consultar essa image.
                region_object = re.search('^ocid1\.image.oc1.(.*)\.', bootVolumeResponse.image_id, re.IGNORECASE).group(1)
                if len(region_object) > 0:
                    if region_object != oci_config['region']:
                        oci_conf = oci.config.from_file(config_file, 'DEFAULT')
                        oci_conf['region'] = region_object
                        if 'signer' in vars() or 'signer' in globals():
                            imageResponse = oci.core.ComputeClient(signer=signer,config=oci_conf,retry_strategy=oci.retry.DEFAULT_RETRY_STRATEGY).get_image(
                                image_id=bootVolumeResponse.image_id
                            ).data
                        else:
                            imageResponse = oci.core.ComputeClient(config=oci_conf,retry_strategy=oci.retry.DEFAULT_RETRY_STRATEGY).get_image(
                                image_id=bootVolumeResponse.image_id
                            ).data
                        del oci_conf
                    else:
                        imageResponse = compute_client.get_image(
                            image_id=bootVolumeResponse.image_id
                        ).data
                else:
                    try:
                        imageResponse = compute_client.get_image(
                            image_id=bootVolumeResponse.image_id
                        ).data
                    except Exception as exc:
                        print(exc)
                        print('boot volume: %s\nocid:\n%s' % ((bootVolumeResponse.display_name).strip(), bootVolumeResponse.image_id))
                        print(color['red'], 'O que aconteceu... (⊙.☉)7')
                        volumes['boot']['image'] = 'no_data'
                        volumes['boot']['os']['name'] = 'no_data'
                        volumes['boot']['os']['version'] = 'no_data'
                        pass

                volumes['boot']['image'] = (imageResponse.display_name).strip()
                volumes['boot']['os']['name'] = imageResponse.operating_system
                volumes['boot']['os']['version'] = imageResponse.operating_system_version
                del(imageResponse, bootVolumeResponse)

            else:
                print(instance.display_name, "onde esta o boot volume dessa maquina !!!!")
                print( instance.id)
                sys.exit(0)

            # -------------------------------------------------------------
            # Procura por Block Volume:
            if instance.id in volumeAttachmentList['block']:
                for Attachment in volumeAttachmentList['block'][instance.id]:
                    # -------------------------------------------------
                    # Podem existir boot volumes anexados como block 
                    # volumes.Nesse caso precisamos verificar o ocid 
                    # para utilizar a chamada correta da API e pegar
                    # as informacoes do volume.
                    try:
                        if re.match('^(ocid1\.bootvolume).*', str(Attachment.volume_id)):
                            # ocid1.bootvolume....
                            volumeResponse = blockStorage_client.get_boot_volume(
                                boot_volume_id=Attachment.volume_id
                            ).data
                        elif re.match('^(ocid1\.volume).*', str(Attachment.volume_id)):
                            # ocid1.volume.oc1....
                            volumeResponse = blockStorage_client.get_volume(
                                volume_id=Attachment.volume_id
                            ).data
                        volumes['block'].append({
                            'name': (volumeResponse.display_name).strip(),
                            'size': volumeResponse.size_in_gbs,
                            'vpu': volumeResponse.vpus_per_gb
                        })
                        del(volumeResponse)
                    except Exception as exc:
                        print(exc)
                        print('instance: %s\nblock_volume_info:\n%s' % ((instance.display_name).strip(), Attachment))
                        print(color['red'], 'O que aconteceu... (⊙.☉)7')
                        pass

            # -----------------------------------------------------------------
            # Sumarizacao das informacoes de block storage:
            block_size = 0
            block_vpu_sum = 0
            for block in volumes['block']:
                block_size += block['size']
                block_vpu_sum += (block['size']*block['vpu'])

            # -----------------------------------------------------------------
            # Grava os dados da instance encontrada no arquivo csv de output:
            with open(instance_list_file, 'a', encoding='utf-8') as f:
                f.write('%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s\n' % (
                    compartment['name'],
                    (instance.display_name).strip(),
                    volumes['boot']['os']['name'],
                    volumes['boot']['os']['version'],
                    region_name,
                    instance.lifecycle_state,
                    instance.shape,
                    instanceType['burstable'],
                    instanceType['preemptible'],
                    instanceType['capacity_reservation'],
                    instanceType['dedicated_vm_host'],
                    instance.shape_config.processor_description,
                    instance.shape_config.ocpus,
                    instance.shape_config.memory_in_gbs,
                    volumes['boot']['image'],
                    volumes['boot']['size'],
                    volumes['boot']['vpu'],
                    len(volumes['block']),
                    block_size,
                    block_vpu_sum,
                    (datetime.now()-time_created).days,
                    instance.id)
                )
            f.close

            volumes = {
                'boot': {'size': '', 'vpu': '', 'image': '', 'os': {'name': '', 'version': ''}},
                'block': list()
            }

            allMetrics = dict()
            listOfMetrics = str()
            makeGraph = True
            with open('.metric_query') as f:
                lines = f.readlines()
                for line in lines:
                    if not re.match('^#', line):
                        (type, query) = line.split('~')
                        query = re.sub(
                            "###AGGREGATION###", aggregation, query)
                        query = re.sub(
                            "###INSTANCE_OCID###", instance.id, query)

                        (min, avg, max, values) = getMetrics(
                            monitoring_client=monitoring_client,
                            query=query,
                            namespace='oci_computeagent',
                            compartment=compartment['id']
                        )
                        if min:
                            listOfMetrics=re.sub('(\, )$', '', f'{type}, {listOfMetrics}')
                            allMetrics[type] = {'min': min, 'avg': avg, 'max': max, 'values': values}
                        else:
                            allMetrics[type] = {'min': 'no_data', 'avg': 'no_data', 'max': 'no_data', 'values': False}
                            makeGraph = False

                if makeGraph:
                    print('    - [%s OK %s] Get metrics %s for %s' % (color['green'],color['clean'], listOfMetrics, (instance.display_name).strip()))
                else:
                    print('    - [%sWARN%s] No metric data for %s' %(color['yellow'], color['clean'], (instance.display_name).strip()))

                # -----------------------------------------------------------------
                # Grava os dados de perfornace da instance no arquivo csv:
                header = str()
                with open(instance_perfornace_file, 'a', encoding='utf-8') as f:
                    csvWriter = csv.writer(f)
                    if header_csv_perf_report:
                        header = ['INSTANCE']
                    row = [(instance.display_name).strip()]
                    for metric_name in allMetrics:
                        for type in allMetrics[metric_name]:
                            if re.match('min|avg|max', type):
                                row.append(allMetrics[metric_name][type])
                                if header_csv_perf_report:
                                    header.append(
                                        (f'{metric_name}_{type}').upper())

                    if header_csv_perf_report:
                        csvWriter.writerow(header)
                        header_csv_perf_report = False
                    csvWriter.writerow(row)
                f.close

                if makeGraph:
                    plotGraph(
                        metrics=allMetrics,
                        file=('%s~%s~%s' % (tenancy_name,(instance.display_name).strip(), instance.id)).lower(),
                        path=file_path
                    )


# -----------------------------------------------------------------
# Inicia o processo de criacao do relatorio em PDF
print('\n# Criando arquivo PDF: Processando graficos...')
pdf = PDF('P', 'mm', 'A4')
pdf.alias_nb_pages()
pdf.add_page(orientation='P')
pdf.set_author('igor nicoli at oracle dot com')

# -----------------------------------------------------------------------------
# Pagina de rosto (cover)
pdf.set_font('Arial', 'BI', 30)
pdf.cell(
    ln=1,
    w=189,  # Largura
    h=265,  # Altura
    border=0,
    align="C",  # Alinhamento centralizado
    txt="Instance Performance Report",
)

# -----------------------------------------------------------------------------
# Coloca as imagens no PDF
# r=root, d=directories, f = files
for r, d, f in os.walk(file_path):
    countdown_files = (len(f)+1)
    count = 0
    for file_name in f:
        countdown_files -= 1
        if re.match(('^%s.*\.png$' % (tenancy_name)), file_name):
            count += 1
            (tenancy, host, ocid) = file_name.split('~')
            print(' - [%s%03d%s] %s' %
                  (color['blue'], countdown_files, color['clean'], host))

            # -----------------------------------------------------------------
            # Titulo do grafico:
            pdf.set_text_color(r=0, g=0, b=255)
            pdf.set_font(family='Arial', style='B', size=17)
            pdf.cell(
                w=0,  # Largura
                h=8,  # Altura
                ln=1,
                border=0,
                txt=('Instance: %s' % host),
            )

            # -----------------------------------------------------------------
            # Subtitulo do grafico (compartment full path)
            pdf.set_text_color(r=200, g=-1, b=-1)
            pdf.set_font(family='Arial', style='I', size=9)
            pdf.cell(
                w=0,  # Largura
                h=4,  # Altura
                ln=1,
                border=0,
                txt=compartment_path[ocid.split('_')[0]]
            )

            # -----------------------------------------------------------------
            # Coloca o grafico da instance no PDF:
            pdf.image(
                h=71,   # Altura
                w=190,  # Largura
                x=None,
                y=None,
                name=os.path.join(file_path, file_name),
                type='PNG'
            )

            # -----------------------------------------------------------------
            # Pula para a proxima pagina depois de colocar 3 graficos na
            # mesma pagina.
            if count == 3:
                pdf.add_page(orientation='P')
                count = 0

            # -----------------------------------------------------------------
            # Remove o arquivo png depois de utiliza-lo.
            os.remove(os.path.join(file_path, file_name))

# -----------------------------------------------------------------------------
# Grava o arquivo PDF em disco:
print(f' `-> Gravando arquivo pdf...\n')
pdf.output(instance_perfornace_report, "F")

#
# Cria um zip com os arquivos csv e pdf do report
print(f'- Criando arquivo zip...')
with ZipFile(zip_output_file, 'w', ZIP_DEFLATED) as zipf:
    zipdir(('%s/%s' % (work_dir, tenancy_name)), zipf)
zipf.close()

print(f'- Limpando workdir...')
os.remove(instance_perfornace_report)
os.remove(instance_list_file)
os.remove(instance_perfornace_file)
os.rmdir(file_path)

print('\nFinished!\n (-̀ᴗ-́)و ̑̑ ')
