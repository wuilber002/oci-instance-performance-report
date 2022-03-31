#!/bin/env python

try:
    import oci
except ImportError as error:
    print(error)
    print("")
    print("OCI libraries not installed. Please install them with 'pip3 install oci'")
    exit(-1)

import re
import sys
import csv
import os.path
from fpdf import FPDF
from genericpath import exists
from datetime import datetime, timedelta

if len(sys.argv) >= 2:
    if not re.match('^principal$', str(sys.argv[1]).lower()):
        config_file = sys.argv[1]
        if not os.path.isfile(config_file):
            print('[ERRO] OCI config file not exist (%s).' % (config_file))
            sys.exit()
        oci_config = oci.config.from_file(config_file,'DEFAULT')
    else:
        oci_config = {}

        # By default this will hit the auth service in the region returned by
        # http://169.254.169.254/opc/v2/instance/region on the instance.
        signer = oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
   
    if len(sys.argv) >= 3:
        compartment_ocid = sys.argv[2]
        print('\n   !!! Executando a pesquisa de instances apenas no compartment !!!')
        print('   !!!     informado e em todas as regioes subscritas...       !!!\n\n')
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
    root_compartment_name = identity_client.get_compartment(root_compartment_ocid).data.name
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
                'name': ('%s/%s' % (root_compartment_name, compartment.name)),
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
def plotGraph(path, file, CPU_x_values, CPU_y_values, MEM_x_values, MEM_y_values, CPU_color, MEM_color, CPU_avg, CPU_mim, CPU_max, MEM_avg, MEM_mim, MEM_max):
    """
    Cria um grafico com os dados recebidos.
    """
    from datetime import datetime
    import matplotlib.pyplot as plt 

    fontLegend = {'family':'serif','color':'black','size':14}

    plt.rcParams['figure.figsize'] = [9, 2.5]
    plt.plot(
        CPU_x_values,
        CPU_y_values,
        color=CPU_color,
        linestyle='solid',
        linewidth=1,
        label=('CPU | mim:%.2f, avg:%.2f, max:%.2f' % (CPU_avg,CPU_mim,CPU_max))
    )
    plt.plot(
        MEM_x_values,
        MEM_y_values,
        color=MEM_color,
        linestyle='solid',
        linewidth=1,
        label=('MEM | mim:%.2f, avg:%.2f, max:%.2f' % (MEM_avg,MEM_mim,MEM_max))
    )
    plt.legend(bbox_to_anchor=(0,1.02,1,0.2), loc="lower left", mode="expand", borderaxespad=0, ncol=3)

    plt.ylabel("Utilization (%)", fontdict=fontLegend)
    plt.xlabel("Time (day)", fontdict=fontLegend)
    plt.ylim(0, 100)
    plt.savefig(
        fname=(('%s/%s.png') % (path,file)),
        dpi=100,
        bbox_inches='tight',
        pad_inches=0.1,
        transparent=False
    )
    plt.close()

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

    sum = 0
    avg = min = max = False
    values = {"x":list(),"y":list()}
    if len(summarize_metrics_data_response.data) > 0:
        for data in (summarize_metrics_data_response.data[0]).aggregated_datapoints:
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
        avg=(sum/len((summarize_metrics_data_response.data[0]).aggregated_datapoints))

    return[sum, min, avg, max, values]

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
                w=40, # Largura
                h=14  # Altura
            )
            self.set_text_color(r=200, g=-1, b=-1) # black
            self.set_font('Arial', 'B', 10) # Arial bold 10
            self.set_y(3) # Move from top
            # Title
            if self.cur_orientation == 'P':
                width=189
            else:
                width=265
            self.cell(
                h=5,       # Altura
                ln=0,
                w=width,   # Largura
                border=0,
                align='C', # Alinhamento centralizado
                txt='Instance Performance Report'
            )
            # Line break
            self.ln(20)

    # Page footer
    def footer(self):
        # Do not print footer on first page 
        if self.page_no() != 1:
            self.set_y(-10) # Position at 1 cm from bottom
            self.set_font('Arial', 'I', 7) # Arial Italic 7
            self.set_text_color(r=200, g=-1, b=-1) # black
            self.cell(0, 10, 'Page ' + str(self.page_no()) + '/{nb}', 0, 0, 'C')

# https://docs.oracle.com/en-us/iaas/Content/Monitoring/Tasks/buildingqueries.htm#MQLEdit
# +-------------+--------------------+
# | aggregation | Max range returned |
# |     1m      |     7  (days)      |
# |     5m      |     30 (days)      |
# |     1h      |     90 (days)      |
# |     1d      |     90 (days)      |
# +-------------+--------------------+
time_range = 30 # Tempo em dias
aggregation = '1h' # Valores possiveis: 1m, 5m, 1h

# -----------------------------------------------------------------------------
# lista de cores para output do script:
color = {
    'yellow': '\033[33m',
     'green': '\033[32m',
      'blue': '\033[34m',
     'clean': '\033[0m'
}

# -----------------------------------------------------------------------------
# Intancia o Identity client :
if 'signer' in vars() or 'signer' in globals():
    identity_client = oci.identity.IdentityClient(
        signer=signer,config=oci_config,
        retry_strategy=oci.retry.DEFAULT_RETRY_STRATEGY
    )
else:
    identity_client = oci.identity.IdentityClient(
        config=oci_config,
        retry_strategy=oci.retry.DEFAULT_RETRY_STRATEGY
    )

# -----------------------------------------------------------------------------
# Lista todas as regioes nas quais o tanancy esta subscrito:
regions = identity_client.list_region_subscriptions(oci_config['tenancy']).data

# -----------------------------------------------------------------------------
# Consulta o nome do tenancy
tenancy_name = identity_client.get_tenancy(oci_config['tenancy']).data.name

# -----------------------------------------------------------------------------
# Diretorio para gravacao temporaria das imagens:
file_path=('./image/%s' % (tenancy_name))
if not exists(file_path):
    os.makedirs(file_path)

# -----------------------------------------------------------------------------
# Monta o nome do arquivo de output com a lista de instances encontrdas:
today = datetime.now()
instance_list_file = ('%s_instance_list_%s.csv' % (tenancy_name,today.strftime("%Y-%m-%d_%H-%M-%S")))
instance_perfornace_file = ('%s_instance_perfornace_data_%s.csv' % (tenancy_name,today.strftime("%Y-%m-%d_%H-%M-%S")))
instance_perfornace_report = ('%s_instance_perfornace_data_%s.pdf' % (tenancy_name,today.strftime("%Y-%m-%d_%H-%M-%S")))

# -----------------------------------------------------------------------------
# Range de tempo para a coleta de dados de performance com o cliente de 
# monitoracao:
start_time=datetime.strptime((today-timedelta(days=time_range)).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),"%Y-%m-%dT%H:%M:%S.%fZ")
end_time=datetime.strptime(today.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),"%Y-%m-%dT%H:%M:%S.%fZ")

# -----------------------------------------------------------------------------
# Grava o header nos arquivos csv:
with open(instance_list_file, 'w', encoding = 'utf-8') as f:
    f.write('compartment,display_name,region,lifecycle_state,shape,ocpus,memory_in_gbs,age(days),ocid\n')
f.close

with open(instance_perfornace_file, 'w', encoding = 'utf-8') as f:
    f.write('instance,CPU_min,CPU_avg,CPU_max,MEM_min,MEM_avg,MEM_max\n')
f.close

# -----------------------------------------------------------------------------
# Inicia a varedura do tenancy vasculhando dentro de cada compartment em todas
# as regions que o tenancy esta subscrito:
compartment_path = dict()
for region_name in [str(es.region_name) for es in regions]:
    print('> %s' % (region_name))
    oci_config['region'] = region_name

    # -------------------------------------------------------------------------
    # Intancia o Compute client :
    if 'signer' in vars() or 'signer' in globals():
        compute_client = oci.core.ComputeClient(
            signer=signer,config=oci_config,
            retry_strategy=oci.retry.DEFAULT_RETRY_STRATEGY
        )
    else:
        compute_client = oci.core.ComputeClient(
            config=oci_config,
            retry_strategy=oci.retry.DEFAULT_RETRY_STRATEGY
        )

    # -----------------------------------------------------------------------------
    # Intancia o Monitoring client :
    if 'signer' in vars() or 'signer' in globals():
        monitoring_client = oci.monitoring.MonitoringClient(
            signer=signer,config=oci_config,
            retry_strategy=oci.retry.DEFAULT_RETRY_STRATEGY
        )
    else:
        monitoring_client = oci.monitoring.MonitoringClient(
            config=oci_config,
            retry_strategy=oci.retry.DEFAULT_RETRY_STRATEGY
        )

    # -------------------------------------------------------------------------
    # Inicia o processamento analisando cada compartment do tenancy:
    for compartment in get_compartments(compartment_ocid):
        print('  - %s' % (compartment['name']))

        for instance in get_instance(compute_client, compartment['id']):
            compartment_path[instance.id] = ('[%s] %s' % (region_name, compartment['name']))
            time_created = datetime.strptime(str(instance.time_created).split(" ")[0], "%Y-%m-%d")

            # -----------------------------------------------------------------
            # Grava os dados da instance encontrada no arquivo csv de output:
            with open(instance_list_file, 'a', encoding = 'utf-8') as f:
                f.write('%s,%s,%s,%s,%s,%s,%s,%s,%s\n' % (
                    compartment['name'],
                    instance.display_name,
                    region_name,
                    instance.lifecycle_state,
                    instance.shape,
                    instance.shape_config.ocpus,
                    instance.shape_config.memory_in_gbs,
                    (datetime.now()-time_created).days,
                    instance.id)
                )
            f.close

            (CPU_sum, CPU_min, CPU_avg, CPU_max, CPU_values) = getMetrics(
                monitoring_client=monitoring_client,
                query=(("CPUUtilization[%s]{resourceId = \"%s\"}.mean()") % (aggregation, instance.id)),
                namespace='oci_computeagent',
                compartment=compartment['id']
            )
            if CPU_sum:
                print('    - [%s OK %s] Get metrics CPU/MEM for %s' % (color['green'],color['clean'],instance.display_name))
                (MEM_sum, MEM_min, MEM_avg, MEM_max, MEM_values) = getMetrics(
                    monitoring_client=monitoring_client,
                    query=(("MemoryUtilization[%s]{resourceId = \"%s\"}.mean()") % (aggregation, instance.id)),
                    namespace='oci_computeagent',
                    compartment=compartment['id']
                )

                # -----------------------------------------------------------------
                # Grava os dados de perfornace da instance no arquivo csv:
                with open(instance_perfornace_file, 'a', encoding = 'utf-8') as f:
                    f.write(
                        '%s,%.2f,%.2f,%.2f,%.2f,%.2f,%.2f\n' % (
                            instance.display_name,
                            MEM_min,MEM_avg,MEM_max,
                            CPU_min,CPU_avg,CPU_max,
                        )
                    )
                f.close

                plotGraph(
                    CPU_color='red',CPU_mim=CPU_min,CPU_avg=CPU_avg,CPU_max=CPU_max,
                    CPU_x_values=CPU_values['x'],CPU_y_values=CPU_values['y'],
                    MEM_color='blue',MEM_avg=MEM_avg,MEM_mim=MEM_min,MEM_max=MEM_max,
                    MEM_x_values=MEM_values['x'],MEM_y_values=MEM_values['y'],
                    file=('%s~%s~%s' % (tenancy_name,instance.display_name,instance.id)).lower(),
                    path=file_path
                )
            else:
                print('    - [%sWARN%s] No metric data for %s' % (color['yellow'],color['clean'],instance.display_name))

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
    w=189, # Largura
    h=265, # Altura
    border=0,
    align="C", # Alinhamento centralizado
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
            print(' - [%s%03d%s] %s' % (color['blue'], countdown_files, color['clean'], host))

            # -----------------------------------------------------------------
            # Titulo do grafico:
            pdf.set_text_color(r=0, g=0, b=255)
            pdf.set_font(family='Arial', style='B', size=17)
            pdf.cell(
                w=0, # Largura
                h=8, # Altura
                ln=1,
                border=0,
                txt=('Instance: %s' % host),
            )

            # -----------------------------------------------------------------
            # Subtitulo do grafico (compartment full path)
            pdf.set_text_color(r=200, g=-1, b=-1)
            pdf.set_font(family='Arial', style='I', size=9)
            pdf.cell(
                w=0, # Largura
                h=4, # Altura
                ln=1,
                border=0,
                txt=compartment_path[re.sub('\.png$', '', ocid)]
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
# Cria a tablela com os dados brutos:
with open(instance_perfornace_file, newline='') as f:

    reader = csv.reader(f)
    pdf.add_page(orientation='L')
    page_width = pdf.w - 2 * pdf.l_margin
    
    # -------------------------------------------------------------------------
    # Definicao o titulo para a tabela:
    pdf.set_font(family='Times', style='B', size=14.0) # Times Bold, size 14
    pdf.set_text_color(r=0, g=-1, b=-1) # Black
    pdf.cell(
        h=0,          # Altura
        align='C',    # Alinhamento centralizado
        w=page_width, # Largura
        txt='Instance Performance Table (CPU & Memory)',
    )
    pdf.ln(10)

    # -------------------------------------------------------------------------
    # Criacao da tabela com os dados de performance:
    pdf.set_font(family='Courier', size=12.0) # Courier, size 12
    col_width = page_width/7
    pdf.ln(1)

    th = pdf.font_size

    for line in reader:
        # ---------------------------------------------------------------------
        # Muda o "style" para "bold" somente na linha do header da tabela:
        if reader.line_num == 1:
            pdf.set_font(family='Courier', style='B', size=12)
            for row in range(len(line)):
                line[row] = line[row].upper()
        else:
            pdf.set_font(family='Courier', style='', size=12)

        # ---------------------------------------------------------------------
        # Popula as colunas da tabela uma a uma:
        pdf.cell(w=145, h=th, txt=str(line[0]), border=1, align='L')
        pdf.cell(w=22,  h=th, txt=str(line[1]), border=1, align='C')
        pdf.cell(w=22,  h=th, txt=str(line[2]), border=1, align='C')
        pdf.cell(w=22,  h=th, txt=str(line[3]), border=1, align='C')
        pdf.cell(w=22,  h=th, txt=str(line[4]), border=1, align='C')
        pdf.cell(w=22,  h=th, txt=str(line[5]), border=1, align='C')
        pdf.cell(w=22,  h=th, txt=str(line[6]), border=1, align='C')
        pdf.ln(th)

# -----------------------------------------------------------------------------
# Grava o arquivo PDF em disco:
pdf.output(instance_perfornace_report, "F")

# -----------------------------------------------------------------------------
# Remove o diretorio temporario das imagens
os.rmdir(file_path)
