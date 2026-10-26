import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import requests
import urllib3
import os
import json
import io
from datetime import datetime, timedelta
from supabase import create_client, Client

# Desabilita avisos SSL para redes internas da PRODEB
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Importações para geração de PDF
from reportlab.lib.pagesizes import A4, landscape
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image as RLImage, KeepTogether
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors

# Configuração da página
st.set_page_config(page_title="Gestor de Sprints PRODEB", layout="wide")

# --- CONEXÃO COM O SUPABASE (Lê do secrets.toml) ---
SUPABASE_URL = st.secrets["supabase"]["url"]
SUPABASE_KEY = st.secrets["supabase"]["key"]

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Lista de Feriados Nacionais / Locais Fixos (YYYY-MM-DD ou MM-DD)
FERIADOS_FIXOS = [
    (1, 1),   # Confraternização Universal
    (4, 21),  # Tiradentes
    (5, 1),   # Dia do Trabalho
    (7, 2),   # Independência da Bahia
    (9, 7),   # Independência do Brasil
    (10, 12), # Nossa Senhora Aparecida / Feriado Nacional
    (11, 2),  # Finados
    (11, 15), # Proclamação da República
    (11, 20), # Consciência Negra
    (12, 25), # Natal
]

def eh_dia_util(dt):
    """Verifica se uma data é dia útil (segunda a sexta e não é feriado cadastrado)."""
    if dt.weekday() >= 5:  # Sábado ou Domingo
        return False
    if (dt.month, dt.day) in FERIADOS_FIXOS:
        return False
    return True

# --- CÁLCULO DE DIAS ÚTEIS ---
def adicionar_dias_uteis(data_inicio, dias_uteis):
    data_atual = data_inicio
    adicionados = 0
    while adicionados < dias_uteis:
        data_atual += timedelta(days=1)
        if eh_dia_util(data_atual):
            adicionados += 1
    return data_atual

def calcular_dias_uteis_entre(data_inicio, data_fim):
    """Calcula o número total de dias úteis entre duas datas (inclusive data_inicio e data_fim)."""
    dias_uteis = 0
    atual = data_inicio
    while atual <= data_fim:
        if eh_dia_util(atual):
            dias_uteis += 1
        atual += timedelta(days=1)
    return dias_uteis

def gerar_dias_uteis_lista(data_inicio, total_dias=15):
    dias = []
    atual = data_inicio
    while len(dias) < total_dias:
        if eh_dia_util(atual):
            dias.append(atual)
        atual += timedelta(days=1)
    return dias

# --- ESTILIZAÇÃO COMPATÍVEL COM DARK/LIGHT MODE ---
def aplicar_estilo_prodeb():
    st.markdown("""
        <style>
        /* Estilo para a Sidebar */
        section[data-testid="stSidebar"] {
            background-color: #0A2540 !important;
            border-top-right-radius: 20px;
            border-bottom-right-radius: 20px;
            padding-top: 10px;
        }

        section[data-testid="stSidebar"] h1, 
        section[data-testid="stSidebar"] h2, 
        section[data-testid="stSidebar"] h3, 
        section[data-testid="stSidebar"] p, 
        section[data-testid="stSidebar"] label,
        section[data-testid="stSidebar"] span {
            color: #FFFFFF !important;
        }

        section[data-testid="stSidebar"] hr {
            border-color: rgba(255, 255, 255, 0.2) !important;
        }

        /* Métricas e Botões Padrão */
        div[data-testid="stMetric"] {
            border-left: 5px solid #0A2540 !important;
            border-radius: 8px !important;
            padding: 12px 16px !important;
            box-shadow: 0px 2px 6px rgba(0,0,0,0.08);
        }

        .stButton > button, 
        div[data-testid="stFormSubmitButton"] > button {
            background-color: #0A2540 !important;
            color: #FFFFFF !important;
            border-radius: 6px !important;
            border: none !important;
            font-weight: 600 !important;
            transition: all 0.3s ease;
        }

        .stButton > button *, 
        div[data-testid="stFormSubmitButton"] > button * {
            color: #FFFFFF !important;
        }

        .stButton > button:hover, 
        div[data-testid="stFormSubmitButton"] > button:hover {
            background-color: #0056b3 !important;
            color: #FFFFFF !important;
            box-shadow: 0px 4px 8px rgba(0,0,0,0.2);
        }
        </style>
    """, unsafe_allow_html=True)

aplicar_estilo_prodeb()

REDMINE_URL = "https://redmine.prodeb.ba.gov.br"
REDMINE_API_KEY = "29247a38237c8f81a65844de91d52c016cc9a8d9"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PATH_LOGO_PRODEB = os.path.join(BASE_DIR, "Logo_Prodeb.jpeg")

# --- PERSISTÊNCIA NO SUPABASE ---
def salvar_dados_em_disco():
    try:
        for nome_sprint, sprint in st.session_state.sprints.items():
            payload = {
                "nome_sprint": nome_sprint,
                "tipo_sprint": sprint.get("tipo_sprint", "Projeto"),
                "data_inicio": sprint["data_inicio"].strftime("%Y-%m-%d"),
                "data_fim": sprint["data_fim"].strftime("%Y-%m-%d"),
                "tarefas": sprint["tarefas"],
                "atualizado_em": datetime.now().isoformat()
            }
            supabase.table("sprints_store").upsert(payload, on_conflict="nome_sprint").execute()
    except Exception as e:
        st.error(f"Erro ao salvar dados no Supabase: {e}")

def carregar_dados_do_disco():
    try:
        response = supabase.table("sprints_store").select("*").execute()
        dados_tabela = response.data
        
        if dados_tabela and len(dados_tabela) > 0:
            sprints_carregadas = {}
            for item in dados_tabela:
                nome_sprint = item["nome_sprint"]
                tipo_sp = item.get("tipo_sprint", "Projeto")
                total_dias = 14 if tipo_sp == "Evolução" else 15
                
                tarefas_tratadas = []
                for t in item["tarefas"]:
                    if "ID" not in t and "id" in t:
                        t["ID"] = str(t["id"])
                    if "Tarefa" not in t and "tarefa" in t:
                        t["Tarefa"] = t["tarefa"]
                    if "Status" not in t and "status" in t:
                        t["Status"] = t["status"]
                    if "Profissional" not in t and "profissional" in t:
                        t["Profissional"] = t["profissional"]
                    if "SP" not in t and "sp" in t:
                        t["SP"] = t["sp"]
                    if "Pausada" not in t and "pausada" in t:
                        t["Pausada"] = t["pausada"]
                    if "Justificativa_Pausa" not in t and "justificativa_pausa" in t:
                        t["Justificativa_Pausa"] = t["justificativa_pausa"]

                    if "Entra_BurnUp" not in t:
                        t["Entra_BurnUp"] = True
                    if "Tags" not in t or not isinstance(t["Tags"], list):
                        t["Tags"] = []
                    if "Motivo_TAG" not in t:
                        t["Motivo_TAG"] = ""
                    if "historico_diario" not in t or not isinstance(t["historico_diario"], dict):
                        t["historico_diario"] = {f"Dia {d}": 0 for d in range(1, total_dias + 1)}
                    
                    t["Tags"] = [
                        str(tag).strip() 
                        for tag in t["Tags"] 
                        if tag is not None and str(tag).strip() and str(tag).strip().lower() not in ["none", "null", "[]", ""]
                    ]
                    tarefas_tratadas.append(t)

                sprints_carregadas[nome_sprint] = {
                    "tipo_sprint": tipo_sp,
                    "data_inicio": datetime.strptime(item["data_inicio"], "%Y-%m-%d").date(),
                    "data_fim": datetime.strptime(item["data_fim"], "%Y-%m-%d").date(),
                    "tarefas": tarefas_tratadas
                }
            return sprints_carregadas
    except Exception as e:
        st.warning(f"Aviso ao carregar do Supabase (utilizando padrão): {e}")

    data_inicio_padrao = datetime(2026, 7, 29).date()
    return {
        "Sprint 7": {
            "tipo_sprint": "Projeto",
            "data_inicio": data_inicio_padrao,
            "data_fim": adicionar_dias_uteis(data_inicio_padrao, 15),
            "tarefas": [
                {"ID": "232712", "Status": "Desenvolvimento: Fazendo", "SP": 5, "Tarefa": "[Faturamento] Migração FIT - Pacientes da Radioterapia para SMS", "Branch": "aguardando", "Profissional": "Rafael Boa Morte do Nascimento", "Pausada": False, "Justificativa_Pausa": "", "Tags": [], "Motivo_TAG": "", "Entra_BurnUp": True, "historico_diario": {f"Dia {d}": 0 for d in range(1, 16)}},
                {"ID": "232725", "Status": "Desenvolvimento: Fazendo", "SP": 5, "Tarefa": "[Faturamento] Migração FIT - Controle de Frequência Oncologia", "Branch": "aguardando", "Profissional": "Juvenal Filho", "Pausada": False, "Justificativa_Pausa": "", "Tags": [], "Motivo_TAG": "", "Entra_BurnUp": True, "historico_diario": {f"Dia {d}": 0 for d in range(1, 16)}},
                {"ID": "232810", "Status": "Desenvolvimento: Fazendo", "SP": 5, "Tarefa": "[Faturamento] Migração FIT - Controle de Frequência APAC de Acompanhamento", "Branch": "aguardando", "Profissional": "Caiou dos Santos Cerqueira", "Pausada": False, "Justificativa_Pausa": "", "Tags": ["Aguardando Informações"], "Motivo_TAG": "Aguardando homologação de fluxos pela SESAB", "Entra_BurnUp": True, "historico_diario": {f"Dia {d}": 0 for d in range(1, 16)}},
                {"ID": "232821", "Status": "Desenvolvimento: A Fazer", "SP": 5, "Tarefa": "[Faturamento] Migração FIT - Gera Arquivo APACs - novo", "Branch": "aguardando", "Profissional": "Caique Souza Souza", "Pausada": False, "Justificativa_Pausa": "", "Tags": [], "Motivo_TAG": "", "Entra_BurnUp": False, "historico_diario": {f"Dia {d}": 0 for d in range(1, 16)}},
                {"ID": "237397", "Status": "Desenvolvimento: A Fazer", "SP": 5, "Tarefa": "[Faturamento] Migração FIT - Funcionalidade Relatórios", "Branch": "aguardando", "Profissional": "CSOL3 / DEV (PRO) / Time A", "Pausada": False, "Justificativa_Pausa": "", "Tags": [], "Motivo_TAG": "", "Entra_BurnUp": False, "historico_diario": {f"Dia {d}": 0 for d in range(1, 16)}},
                {"ID": "220839", "Status": "Homologação: Fazendo", "SP": 5, "Tarefa": "APAC Radioterapia - Programar Encerramento", "Branch": "aguardando", "Profissional": "Leandro Reis Costa", "Pausada": False, "Justificativa_Pausa": "", "Tags": [], "Motivo_TAG": "", "Entra_BurnUp": True, "historico_diario": {f"Dia {d}": 80 if d == 1 else 0 for d in range(1, 16)}},
            ]
        }
    }

# --- FUNÇÕES AUXILIARES DE GERENCIAMENTO DE SPRINTS ATIVAS ---
def obter_sprints_ativas(dt_hoje=None):
    if dt_hoje is None:
        dt_hoje = datetime.today().date()
    
    sprints_ativas = []
    for nome, dados in st.session_state.sprints.items():
        if dados["data_inicio"] <= dt_hoje <= dados["data_fim"]:
            sprints_ativas.append(nome)
    return sprints_ativas

def obter_sprint_padrao_principal(dt_hoje=None):
    if dt_hoje is None:
        dt_hoje = datetime.today().date()
    
    sprints_projetos = [
        (nome, dados) for nome, dados in st.session_state.sprints.items()
        if dados.get("tipo_sprint", "Projeto") == "Projeto"
    ]
    
    if sprints_projetos:
        sprints_projetos.sort(key=lambda x: x[1]["data_inicio"], reverse=True)
        return sprints_projetos[0][0]
        
    todas = list(st.session_state.sprints.keys())
    return todas[-1] if todas else None

def verificar_impedimentos(tarefa):
    alertas = []
    tags_brutas = tarefa.get("Tags", [])
    if not isinstance(tags_brutas, list):
        tags_brutas = [tags_brutas]
        
    tags_limpas = [
        str(t).strip().lower() 
        for t in tags_brutas 
        if t is not None and str(t).strip() and str(t).strip().lower() not in ["none", "null", "[]", ""]
    ]
    
    motivo = tarefa.get("Motivo_TAG", "").strip()
    motivo_str = f" - Motivo: {motivo}" if motivo else ""

    if "impedimento" in tags_limpas:
        alertas.append(f"Impedimento{motivo_str}")
        
    if any(t in tags_limpas for t in ["aguardando informações", "aguardando informacao"]):
        alertas.append(f"Aguardando Informações{motivo_str}")
        
    if tarefa.get("Pausada") or tarefa.get("pausada"):
        just = tarefa.get("Justificativa_Pausa", "") or tarefa.get("justificativa_pausa", "")
        just_str = f" - Motivo: {just.strip()}" if just.strip() else ""
        alertas.append(f"Tarefa Pausada{just_str}")
            
    return alertas

def buscar_dados_redmine(issue_id, tipo_sprint="Projeto"):
    url = f"{REDMINE_URL}/issues/{issue_id}.json"
    headers = {"X-Redmine-API-Key": REDMINE_API_KEY}
    try:
        response = requests.get(url, headers=headers, timeout=3, verify=False)
        if response.status_code == 200:
            data = response.json().get("issue", {})
            status = data.get("status", {}).get("name", "N/A")
            titulo = data.get("subject", "Sem título")
            profissional = data.get("assigned_to", {}).get("name", "-")
            
            tags = []
            for field in data.get("custom_fields", []):
                name = str(field.get("name", "")).lower()
                if "tag" in name or "alerta" in name or "impedimento" in name:
                    val = field.get("value")
                    if val:
                        if isinstance(val, list):
                            for v in val:
                                v_str = str(v).strip()
                                if v_str and v_str.lower() not in ["none", "null", "[]", ""]:
                                    tags.append(v_str)
                        else:
                            val_str = str(val).strip()
                            if val_str and val_str.lower() not in ["none", "null", "[]", ""]:
                                tags.append(val_str)

            sp = 0
            if tipo_sprint == "Evolução":
                for field in data.get("custom_fields", []):
                    f_name = str(field.get("name", "")).lower()
                    if "story point" in f_name or "sp" in f_name:
                        if field.get("value"):
                            try:
                                sp = int(float(str(field["value"]).replace(",", ".")))
                            except (ValueError, TypeError):
                                sp = 0
            else:
                if "estimated_hours" in data and data["estimated_hours"]:
                    sp = int(data["estimated_hours"])
                else:
                    for field in data.get("custom_fields", []):
                        f_name = str(field.get("name", "")).lower()
                        if "story point" in f_name or "sp" in f_name:
                            if field.get("value"):
                                try:
                                    sp = int(float(str(field["value"]).replace(",", ".")))
                                except (ValueError, TypeError):
                                    sp = 0
            return {"Status": status, "SP": sp if sp > 0 else 5, "Tarefa": titulo, "Profissional": profissional, "Tags": tags}
    except Exception:
        pass
    return None

def sincronizar_sprint_com_redmine(nome_sprint):
    if nome_sprint in st.session_state.sprints:
        tipo_sp = st.session_state.sprints[nome_sprint].get("tipo_sprint", "Projeto")
        for tarefa in st.session_state.sprints[nome_sprint]["tarefas"]:
            tid = tarefa.get("ID") or tarefa.get("id")
            if tid:
                dados_novos = buscar_dados_redmine(tid, tipo_sprint=tipo_sp)
                if dados_novos:
                    tarefa["Status"] = dados_novos["Status"]
                    tarefa["Profissional"] = dados_novos["Profissional"]
                    tarefa["Tarefa"] = dados_novos["Tarefa"]
                    
                    tags_locais = [str(t).strip() for t in tarefa.get("Tags", []) if str(t).strip()]
                    tags_redmine = [str(t).strip() for t in dados_novos.get("Tags", []) if str(t).strip()]
                    
                    tags_unificadas = list(dict.fromkeys(tags_locais + tags_redmine))
                    tarefa["Tags"] = tags_unificadas

                    if dados_novos["SP"] > 0:
                        tarefa["SP"] = dados_novos["SP"]
        salvar_dados_em_disco()

def obter_situacao_e_estilo_sprint(profissional, status_redmine):
    prof = str(profissional).strip()
    status = str(status_redmine).strip()
    
    status_homologacao = ["Homologação: Fazendo", "Homologação: Feito"]
    
    if prof == "SESAB SESAB" and status in status_homologacao:
        return "Em validação SESAB", "#FFFFFF", colors.HexColor('#0056B3')
        
    if prof == "HCPA HCPA" and status in status_homologacao:
        return "Em validação HCPA", "#FFFFFF", colors.HexColor('#2ECC71')
        
    profissionais_prodeb_valida = ["CSOL3 / AGHUse / PO", "Elane Ferreira Faneca", "Lucy Souza Santos"]
    if prof in profissionais_prodeb_valida:
        return "Em validação PRODEB", "#FFFFFF", colors.HexColor('#8E44AD')
        
    return "Em andamento PRODEB", "#000000", colors.HexColor('#F1C40F')

# --- FUNÇÃO DE GERAÇÃO DO RELATÓRIO PDF ---
def gerar_pdf_relatorio(nome_sprint, fig_sprint):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        rightMargin=30, leftMargin=30, topMargin=30, bottomMargin=30
    )
    story = []
    styles = getSampleStyleSheet()

    style_title = ParagraphStyle(
        'DocTitle',
        parent=styles['Heading1'],
        fontName='Helvetica-Bold',
        fontSize=18,
        textColor=colors.HexColor('#0A2540'),
        spaceAfter=4
    )

    style_subtitle = ParagraphStyle(
        'DocSubTitle',
        parent=styles['Heading2'],
        fontName='Helvetica-Bold',
        fontSize=12,
        textColor=colors.HexColor('#0A2540'),
        spaceAfter=10
    )

    style_normal = ParagraphStyle(
        'DocNormal',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=9,
        leading=11
    )

    dados_sprint = st.session_state.sprints[nome_sprint]
    dt_fim_relatorio = dados_sprint['data_fim']

    if os.path.exists(PATH_LOGO_PRODEB):
        img = RLImage(PATH_LOGO_PRODEB, width=120, height=35)
        header_data = [[img, Paragraph(f"<b>RELATÓRIO EXECUTIVO - {nome_sprint.upper()}</b>", style_title)]]
    else:
        header_data = [["", Paragraph(f"<b>RELATÓRIO EXECUTIVO - {nome_sprint.upper()}</b>", style_title)]]

    t_header = Table(header_data, colWidths=[130, 650])
    t_header.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('ALIGN', (1,0), (1,0), 'LEFT'),
    ]))
    story.append(t_header)
    story.append(Spacer(1, 12))

    meta_info = [
        [
            Paragraph(f"<b>Gerado em:</b> {datetime.now().strftime('%d/%m/%Y %H:%M')}", style_normal),
            Paragraph(f"<b>Início da Sprint:</b> {dados_sprint['data_inicio'].strftime('%d/%m/%Y')}", style_normal),
            Paragraph(f"<b>Término Sprint:</b> {dt_fim_relatorio.strftime('%d/%m/%Y')}", style_normal)
        ]
    ]
    t_meta = Table(meta_info, colWidths=[260, 260, 260])
    t_meta.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#EAECEE')),
        ('PADDING', (0,0), (-1,-1), 6),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#BDC3C7')),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ]))
    story.append(t_meta)
    story.append(Spacer(1, 10))

    dt_hoje = datetime.today().date()
    if dt_hoje > dt_fim_relatorio:
        alertas_prazos = "Status de Prazo: Sprint Finalizada"
    else:
        dias_uteis_restantes = calcular_dias_uteis_entre(dt_hoje, dt_fim_relatorio)
        if dias_uteis_restantes > 2:
            alertas_prazos = f"Status de Prazo: Regular (Faltam {dias_uteis_restantes} dias úteis)"
        else:
            alertas_prazos = f"Atenção: Reta final da Sprint (Faltam {dias_uteis_restantes} dias úteis)"
    
    impedimentos_text = []
    for t in dados_sprint["tarefas"]:
        probs = verificar_impedimentos(t)
        if probs:
            status_str = ", ".join(probs)
            tid = t.get("ID") or t.get("id")
            ttitulo = t.get("Tarefa") or t.get("tarefa")
            impedimentos_text.append(f"• <b>#{tid}</b> - {ttitulo}<br/>&nbsp;&nbsp;&nbsp;&nbsp;<b>Status:</b> {status_str}")

    txt_bloqueios = "<br/><br/>".join(impedimentos_text) if impedimentos_text else "Nenhum bloqueio ou impedimento ativo."

    alertas_table_data = [
        [Paragraph("<b>Alertas de Prazo & Calendário</b>", style_normal), Paragraph("<b>Bloqueios e Impedimentos Identificados</b>", style_normal)],
        [Paragraph(alertas_prazos, style_normal), Paragraph(txt_bloqueios, style_normal)]
    ]
    t_alertas = Table(alertas_table_data, colWidths=[390, 390])
    t_alertas.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (0,0), colors.HexColor('#F4F6F7')),
        ('BACKGROUND', (1,0), (1,0), colors.HexColor('#F4F6F7')),
        ('PADDING', (0,0), (-1,-1), 6),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#BDC3C7')),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
    ]))
    story.append(t_alertas)
    story.append(Spacer(1, 12))

    story.append(Paragraph("Acompanhamento das Tarefas", style_subtitle))
    
    headers_tabela = ["ID", "Tarefa", "Profissional", "SP", "Status Redmine", "Situação na Sprint"]
    rows_tabela = [headers_tabela]

    t_style = [
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#0A2540')),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('ALIGN', (0,0), (-1,0), 'CENTER'),
        ('ALIGN', (3,1), (3,-1), 'CENTER'),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#D5D8DC')),
        ('PADDING', (0,0), (-1,-1), 5),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ]

    for idx, t in enumerate(dados_sprint["tarefas"], start=1):
        prof = t.get("Profissional") or t.get("profissional", "-")
        st_redmine = t.get("Status") or t.get("status", "N/A")
        tid = t.get("ID") or t.get("id")
        ttitulo = t.get("Tarefa") or t.get("tarefa", "")
        tsp = t.get("SP") or t.get("sp", 5)
        
        sit_label, txt_color, c_bg = obter_situacao_e_estilo_sprint(prof, st_redmine)
        cell_sit = f'<font color="{txt_color}"><b>{sit_label}</b></font>'
        
        rows_tabela.append([
            tid,
            Paragraph(ttitulo[:50], style_normal),
            Paragraph(prof, style_normal),
            str(tsp),
            Paragraph(st_redmine, style_normal),
            Paragraph(cell_sit, style_normal)
        ])

        t_style.append(('BACKGROUND', (5, idx), (5, idx), c_bg))
        t_style.append(('ALIGN', (5, idx), (5, idx), 'CENTER'))

    t_tarefas = Table(rows_tabela, colWidths=[55, 260, 160, 35, 130, 140])
    t_tarefas.setStyle(TableStyle(t_style))
    story.append(t_tarefas)
    story.append(Spacer(1, 15))

    if fig_sprint:
        try:
            img_bytes = fig_sprint.to_image(format="png", width=780, height=320)
            img_buffer = io.BytesIO(img_bytes)
            story.append(KeepTogether([
                Paragraph("Gráfico de Desempenho da Sprint", style_subtitle),
                RLImage(img_buffer, width=780, height=320),
                Spacer(1, 15)
            ]))
        except Exception:
            pass

    doc.build(story)
    buffer.seek(0)
    return buffer

# --- ESTADO INICIAL DA SESSÃO ---
if "sprints" not in st.session_state:
    st.session_state.sprints = carregar_dados_do_disco()

if "modal_inicial_exibido" not in st.session_state:
    st.session_state.modal_inicial_exibido = False

# --- MODAIS DE GERENCIAMENTO ---
@st.dialog("📝 Renomear Sprint")
def modal_renomear_sprint(nome_atual):
    st.write(f"Informe o novo nome para a **{nome_atual}**:")
    novo_nome = st.text_input("Novo Nome da Sprint", value=nome_atual)
    
    col_salvar, col_cancelar = st.columns(2)
    if col_salvar.button("💾 Confirmar", use_container_width=True):
        novo_nome_limpo = novo_nome.strip()
        if not novo_nome_limpo:
            st.error("O nome não pode ser vazio.")
        elif novo_nome_limpo != nome_atual and novo_nome_limpo in st.session_state.sprints:
            st.error("Já existe uma Sprint com este nome!")
        elif novo_nome_limpo != nome_atual:
            dados_sprint = st.session_state.sprints.pop(nome_atual)
            st.session_state.sprints[novo_nome_limpo] = dados_sprint
            
            try:
                supabase.table("sprints_store").delete().eq("nome_sprint", nome_atual).execute()
            except Exception as e:
                st.error(f"Erro ao atualizar registro antigo no Supabase: {e}")
                
            salvar_dados_em_disco()
            st.success(f"Sprint renomeada para '{novo_nome_limpo}' com sucesso!")
            st.rerun()

@st.dialog("➕ Incluir Tarefas na Sprint")
def modal_incluir_tarefas(nome_sprint):
    st.write(f"Adicione novas tarefas do Redmine à **{nome_sprint}**:")
    
    if "lista_ids_modal" not in st.session_state:
        st.session_state.lista_ids_modal = [""]

    ids_informados = []
    for i, val in enumerate(st.session_state.lista_ids_modal):
        novo_val = st.text_input(f"ID da Tarefa Redmine #{i+1}", value=val, key=f"input_id_tarefa_{i}", placeholder="Ex: 232824")
        st.session_state.lista_ids_modal[i] = novo_val
        if novo_val.strip():
            ids_informados.append(novo_val.strip())

    col_mais, col_menos = st.columns(2)
    
    def adicionar_campo():
        st.session_state.lista_ids_modal.append("")

    def remover_campo():
        if len(st.session_state.lista_ids_modal) > 1:
            st.session_state.lista_ids_modal.pop()

    col_mais.button("➕ Adicionar outro campo", on_click=adicionar_campo, use_container_width=True)
    col_menos.button("➖ Remover último campo", on_click=remover_campo, use_container_width=True)

    st.markdown("---")
    if st.button("💾 Confirmar e Incluir na Sprint", type="primary", use_container_width=True):
        if not ids_informados:
            st.warning("Informe pelo menos um ID de tarefa válido.")
        else:
            dados_sprint = st.session_state.sprints[nome_sprint]
            tipo_sp = dados_sprint.get("tipo_sprint", "Projeto")
            total_dias = 14 if tipo_sp == "Evolução" else 15
            
            for i_id in ids_informados:
                d = buscar_dados_redmine(i_id, tipo_sprint=tipo_sp)
                if not d:
                    d = {
                        "Status": "Desenvolvimento: Fazendo", 
                        "SP": 5, 
                        "Tarefa": f"Tarefa #{i_id}", 
                        "Profissional": "-", 
                        "Tags": []
                    }
                d["ID"] = i_id
                d["Branch"] = "aguardando"
                d["Pausada"] = False
                d["Justificativa_Pausa"] = ""
                d["Entra_BurnUp"] = True
                d["historico_diario"] = {f"Dia {d}": 0 for d in range(1, total_dias + 1)}
                
                dados_sprint["tarefas"].append(d)
            
            salvar_dados_em_disco()
            st.session_state.lista_ids_modal = [""]
            st.success("Tarefa(s) incluída(s) com sucesso como as últimas da sprint!")
            st.rerun()

@st.dialog("🔐 Confirmação de Gestor para Exclusão")
def modal_excluir_sprint(nome_sprint):
    st.warning(f"⚠️ Atenção: Você está prestes a excluir permanentemente a **{nome_sprint}**.")
    st.write("Esta ação não poderá ser desfeita. Digite o código de gestor para autorizar:")
    
    codigo_input = st.text_input("Código do Gestor", type="password", placeholder="Digite a senha de exclusão")
    
    col_btn_exc, col_btn_can = st.columns(2)
    if col_btn_exc.button("🗑️ Confirmar Exclusão", type="primary", use_container_width=True):
        if codigo_input == st.secrets["gestor"]["senha_exclusao"]:
            del st.session_state.sprints[nome_sprint]
            try:
                supabase.table("sprints_store").delete().eq("nome_sprint", nome_sprint).execute()
            except Exception as e:
                st.error(f"Erro ao remover sprint do Supabase: {e}")
            
            st.success(f"Sprint '{nome_sprint}' excluída com sucesso!")
            st.rerun()
        else:
            st.error("❌ Código de gestor incorreto! A exclusão foi cancelada.")

@st.dialog("Ajustar Sprint?")
def modal_inicial():
    sprints_disponiveis = list(st.session_state.sprints.keys())
    
    if not sprints_disponiveis:
        st.warning("Nenhuma Sprint cadastrada para ajustar.")
        if st.button("Continuar"):
            st.session_state.modal_inicial_exibido = True
            st.rerun()
        return

    st.write("Configure os dados ou adicione tarefas à Sprint desejada.")

    with st.form("form_modal_inicio"):
        sprint_alvo = st.selectbox(
            "Selecione a Sprint a ser ajustada*", 
            options=sprints_disponiveis,
            index=0
        )
        
        sprint_ref = st.session_state.sprints[sprint_alvo]
        tipo_sp = sprint_ref.get("tipo_sprint", "Projeto")
        dt_inicio_atual = sprint_ref.get("data_inicio", datetime.today().date())

        dt_inicio = st.date_input("Data de Início da Sprint*", value=dt_inicio_atual)
        
        total_dias = 14 if tipo_sp == "Evolução" else 15
        dt_fim_calculada = adicionar_dias_uteis(dt_inicio, total_dias)
        st.info(f"📅 **Período projetado ({tipo_sp}):** {dt_inicio.strftime('%d/%m/%Y')} até {dt_fim_calculada.strftime('%d/%m/%Y')}")
        
        st.markdown("---")
        deseja_add = st.radio("Deseja incluir uma nova tarefa nesta Sprint?", ["Não", "Sim"], index=0)
        issue_id = st.text_input("Número/ID da Tarefa no Redmine", placeholder="Ex: 232824")
        branch = st.text_input("Nome da Filial / Branch", value="aguardando")
        
        col_b1, col_b2 = st.columns(2)
        btn_confirmar = col_b1.form_submit_button("💾 Salvar e Continuar")
        btn_continuar = col_b2.form_submit_button("➡️ Apenas Continuar")
        
        if btn_confirmar or btn_continuar:
            if btn_confirmar:
                st.session_state.sprints[sprint_alvo]["data_inicio"] = dt_inicio
                st.session_state.sprints[sprint_alvo]["data_fim"] = dt_fim_calculada
                
                if deseja_add == "Sim" and issue_id.strip():
                    dados = buscar_dados_redmine(issue_id.strip(), tipo_sprint=tipo_sp)
                    if not dados:
                        dados = {
                            "Status": "Desenvolvimento: Fazendo", 
                            "SP": 5, 
                            "Tarefa": f"Tarefa #{issue_id.strip()}", 
                            "Profissional": "-", 
                            "Tags": []
                        }
                    dados["ID"] = str(issue_id.strip())
                    dados["Branch"] = branch
                    dados["Pausada"] = False
                    dados["Justificativa_Pausa"] = ""
                    dados["Entra_BurnUp"] = True
                    dados["historico_diario"] = {f"Dia {d}": 0 for d in range(1, total_dias + 1)}
                    
                    st.session_state.sprints[sprint_alvo]["tarefas"].append(dados)
                    
                salvar_dados_em_disco()
            
            st.session_state.modal_inicial_exibido = True
            st.rerun()

@st.dialog("✨ Criar Nova Sprint Parametrizada")
def modal_criar_sprint():
    with st.form("form_nova_sprint"):
        tipo_sprint = st.radio("Selecione o Tipo de Sprint*", ["Projeto", "Evolução"], index=0, horizontal=True)
        nome_sprint = st.text_input("Nome da Sprint*", value=f"Sprint {len(st.session_state.sprints) + 7}")
        dt_inicio = st.date_input("Data de Início*", value=datetime.today().date())
        
        total_dias = 14 if tipo_sprint == "Evolução" else 15
        dt_fim = adicionar_dias_uteis(dt_inicio, total_dias)
        
        if tipo_sprint == "Projeto":
            st.info(f"🗓️ **Projeto (15 Dias Úteis):** 10 Dias Dev + 5 Dias PO/Qualidade (Término em {dt_fim.strftime('%d/%m/%Y')})")
        else:
            st.info(f"🗓️ **Evolução (14 Dias Úteis):** 13 Dias Dev + 1 Dia Validação PO (Término em {dt_fim.strftime('%d/%m/%Y')})")
        
        ids_iniciais = st.text_area("IDs das Tarefas do Redmine (Separadas por vírgula)", placeholder="232712, 232725")
        btn_criar = st.form_submit_button("Criar Sprint")
        
        if btn_criar:
            if not nome_sprint.strip():
                st.error("Informe o nome da Sprint.")
                return
            
            novas_tarefas = []
            lista_ids = [i.strip() for i in ids_iniciais.replace("\n", ",").split(",") if i.strip()]
            
            for i_id in lista_ids:
                d = buscar_dados_redmine(i_id, tipo_sprint=tipo_sprint)
                if d:
                    d["ID"] = i_id
                    d["Branch"] = "aguardando"
                    d["Pausada"] = False
                    d["Justificativa_Pausa"] = ""
                    d["Entra_BurnUp"] = True
                    d["historico_diario"] = {f"Dia {d}": 0 for d in range(1, total_dias + 1)}
                    novas_tarefas.append(d)
                else:
                    novas_tarefas.append({"ID": i_id, "Status": "Desenvolvimento: Fazendo", "SP": 5, "Tarefa": f"Tarefa {i_id}", "Branch": "aguardando", "Pausada": False, "Justificativa_Pausa": "", "Tags": [], "Entra_BurnUp": True, "historico_diario": {f"Dia {d}": 0 for d in range(1, total_dias + 1)}})
            
            st.session_state.sprints[nome_sprint] = {
                "tipo_sprint": tipo_sprint,
                "data_inicio": dt_inicio,
                "data_fim": dt_fim,
                "tarefas": novas_tarefas
            }
            salvar_dados_em_disco()
            st.success(f"{nome_sprint} criada com sucesso!")
            st.rerun()

if not st.session_state.modal_inicial_exibido:
    st.session_state.modal_inicial_exibido = True
    modal_inicial()

# --- PAINEL LATERAL (SIDEBAR) ---
with st.sidebar:
    st.title("⚙️ Painel de Controle")
    st.subheader("🔔 Alertas da Sprint")
    dt_hoje_sidebar = datetime.today().date()
    
    sprints_lista = list(st.session_state.sprints.keys())
    sprints_ativas = obter_sprints_ativas(dt_hoje_sidebar)
    
    if sprints_ativas:
        for s_nome in sprints_ativas:
            s_dados = st.session_state.sprints[s_nome]
            dt_fim_po_sb = s_dados["data_fim"]
            
            dias_po = calcular_dias_uteis_entre(dt_hoje_sidebar, dt_fim_po_sb)
            
            if 0 <= dias_po <= 2:
                st.error(f"🚨 **Fim PO/Qualidade ({s_nome}):** Faltam apenas {dias_po} dia(s) úteis!")
            else:
                st.success(f"✅ Prazo de Fim PO/Qualidade da **{s_nome}** está regular ({dias_po} dia(s) úteis restantes).")
    else:
        sprint_ref_nome = obter_sprint_padrao_principal(dt_hoje_sidebar)
        if sprint_ref_nome:
            st.info(f"ℹ️ Nenhuma sprint em andamento.\nÚltima/Referência: **{sprint_ref_nome}**")
        else:
            st.info("ℹ️ Nenhuma sprint cadastrada no momento.")

    st.markdown("---")
    st.subheader("⚠️ Painel de Bloqueios & Alertas")
    
    impedimentos_encontrados = []
    sprints_alvo_bloqueios = sprints_ativas if sprints_ativas else ([obter_sprint_padrao_principal(dt_hoje_sidebar)] if obter_sprint_padrao_principal(dt_hoje_sidebar) else [])
    
    for s_nome in sprints_alvo_bloqueios:
        if s_nome in st.session_state.sprints:
            for t in st.session_state.sprints[s_nome]["tarefas"]:
                probs = verificar_impedimentos(t)
                if probs:
                    tid = t.get("ID") or t.get("id")
                    ttitulo = t.get("Tarefa") or t.get("tarefa", "")
                    impedimentos_encontrados.append((s_nome, tid, ttitulo, ", ".join(probs)))
    
    if impedimentos_encontrados:
        st.error(f"🚨 **{len(impedimentos_encontrados)} Tarefa(s) Requerem Atenção:**")
        for snome, tid, ttitulo, tprobs in impedimentos_encontrados:
            st.markdown(f"• **[{snome}] #{tid}**: {ttitulo[:25]}...\n  └ *Motivo: {tprobs}*")
    else:
        st.info("✅ Nenhum impedimento detectado nas sprints ativas.")

    st.markdown("---")
    st.subheader("📄 Emitir Relatório")

    if sprints_lista:
        sprint_pdf_selecionada = st.selectbox(
            "Selecione a Sprint:",
            options=sprints_lista,
            key="sb_sprint_pdf_select"
        )
        
        fig_sp_ref = st.session_state.get(f"fig_sprint_{sprint_pdf_selecionada}")
        pdf_bytes = gerar_pdf_relatorio(sprint_pdf_selecionada, fig_sp_ref)
        
        st.download_button(
            label="📄 Gerar",
            data=pdf_bytes,
            file_name=f"Relatorio_Executivo_{sprint_pdf_selecionada.replace(' ', '_')}.pdf",
            mime="application/pdf",
            use_container_width=True,
            type="primary"
        )
        st.markdown("---")

        with st.expander("⚠️ Régua do Conceito de Pronto (% Total)", expanded=False):
            regua_dados = {
                "Etapa da Atividade": [
                    "Início da Análise e Entendimento", "Conclusão da Análise e Entendimento",
                    "Desenvolvimento Inicial", "Desenvolvimento Parcial", "Desenvolvimento Completo",
                    "Desenvolvimento de testes - Início", "Desenvolvimento de testes - Conclusão",
                    "Atividade de Repositório Prodeb", "Documentação", "Teste integrado na release / Homologação"
                ],
                "% Pronto": ["5%", "10%", "20%", "30%", "40%", "45%", "50%", "60%", "70%", "80%"]
            }
            df_regua = pd.DataFrame(regua_dados)
            st.dataframe(df_regua, hide_index=True, use_container_width=True)

# --- CABEÇALHO (LOGO À ESQUERDA EM TAMANHO DOBRADO 320px E TÍTULO) ---
col_logo_left, col_titulo = st.columns([4, 8])

with col_logo_left:
    if os.path.exists(PATH_LOGO_PRODEB):
        st.image(PATH_LOGO_PRODEB, width=320)

with col_titulo:
    st.title("📊 Acompanhamento Diário - Sprints")

col_head1, col_head2 = st.columns([8, 2])
with col_head2:
    if st.button("➕ Criar Nova Sprint", use_container_width=True):
        modal_criar_sprint()

st.markdown("---")

# --- SELETOR DE SPRINTS, RENOMEAR, INCLUIR TAREFAS E EXCLUIR (LADO A LADO) ---
nomes_sprints = list(st.session_state.sprints.keys())

if not nomes_sprints:
    st.warning("Nenhuma Sprint cadastrada. Clique em '➕ Criar Nova Sprint' para começar.")
else:
    sprint_padrao_nome = obter_sprint_padrao_principal()
    
    if sprint_padrao_nome and sprint_padrao_nome in nomes_sprints:
        nomes_sprints.remove(sprint_padrao_nome)
        nomes_sprints.insert(0, sprint_padrao_nome)

    col_sel, col_ren, col_inc, col_del = st.columns([4, 3, 3, 3])
    
    with col_sel:
        nome_sprint = st.selectbox(
            "📋 **Selecionar Sprint:**",
            options=nomes_sprints,
            index=0,
            key="selectbox_sprint_principal"
        )

    if nome_sprint:
        dados_sprint = st.session_state.sprints[nome_sprint]
        tipo_sprint = dados_sprint.get("tipo_sprint", "Projeto")
        total_dias_sprint = 14 if tipo_sprint == "Evolução" else 15

        with col_ren:
            st.write("") 
            st.write("")
            if st.button("📝 Renomear", key=f"btn_ren_sprint_{nome_sprint}", use_container_width=True):
                modal_renomear_sprint(nome_sprint)

        with col_inc:
            st.write("") 
            st.write("")
            if st.button("➕ Incluir Tarefas", key=f"btn_inc_tarefa_{nome_sprint}", use_container_width=True):
                modal_incluir_tarefas(nome_sprint)

        with col_del:
            st.write("") 
            st.write("")
            if st.button("🗑️ Excluir", key=f"btn_del_sprint_{nome_sprint}", type="primary", use_container_width=True):
                modal_excluir_sprint(nome_sprint)

        st.markdown("---")

        dt_inicio = dados_sprint["data_inicio"]
        dt_fim = dados_sprint["data_fim"]
        dias_uteis_lista = gerar_dias_uteis_lista(dt_inicio, total_dias_sprint)
        
        dev_days_idx = 12 if tipo_sprint == "Evolução" else 9
        dt_fim_dev = dias_uteis_lista[dev_days_idx]

        col_m1, col_m2, col_m3, col_m4 = st.columns([3, 3, 3, 2])
        col_m1.metric("🗓️ Início da Sprint", dt_inicio.strftime("%d/%m/%Y"), delta=f"Tipo: {tipo_sprint}")
        
        if tipo_sprint == "Evolução":
            col_m2.metric("🛠️ Fim Dev (13º Dia Útil)", dt_fim_dev.strftime("%d/%m/%Y"))
            col_m3.metric("🧪 Validação PO (14º Dia Útil)", dt_fim.strftime("%d/%m/%Y"))
        else:
            col_m2.metric("🛠️ Fim Dev (10º Dia Útil)", dt_fim_dev.strftime("%d/%m/%Y"))
            col_m3.metric("🧪 Fim PO/Qualidade (15º Dia Útil)", dt_fim.strftime("%d/%m/%Y"))

        with col_m4:
            st.write("")
            if st.button("🔄 Sincronizar Redmine", key=f"btn_sync_{nome_sprint}", use_container_width=True):
                with st.spinner("🔄 Atualizando dados com o Redmine..."):
                    sincronizar_sprint_com_redmine(nome_sprint)
                    st.success("Sincronização concluída!")
                    st.rerun()

        st.markdown("---")

        # --- GESTÃO DE TAREFAS ---
        with st.expander(f"🛠️ Gerenciar Tarefas ({nome_sprint}) - Editar, Pausar ou Excluir", expanded=False):
            tarefas_sprint = dados_sprint["tarefas"]
            if tarefas_sprint:
                ids_t = [f"#{t.get('ID') or t.get('id')} - {(t.get('Tarefa') or t.get('tarefa', ''))[:35]}..." for t in tarefas_sprint]
                sel_t = st.selectbox("Selecione um(a):", ids_t, key=f"sel_gerenciar_{nome_sprint}")
                idx_t = ids_t.index(sel_t)
                tarefa_sel = tarefas_sprint[idx_t]

                tab_editar, tab_pausar, tab_tags, tab_excluir = st.tabs(["✏️ Editar Tarefa", "⏸️ Pausar Tarefa", "🏷️ TAGs", "🗑️ Excluir Tarefa"])

                with tab_editar:
                    with st.form(key=f"form_editar_{nome_sprint}_{idx_t}"):
                        novo_titulo = st.text_input("Título:", value=tarefa_sel.get("Tarefa") or tarefa_sel.get("tarefa", ""))
                        col_e1, col_e2, col_e3 = st.columns(3)
                        novo_status = col_e1.text_input("Status:", value=tarefa_sel.get("Status") or tarefa_sel.get("status", ""))
                        novo_sp = col_e2.number_input("SP (Story Points):", value=int(tarefa_sel.get("SP") or tarefa_sel.get("sp", 5)), min_value=1)
                        nova_branch = col_e3.text_input("Filial / Branch:", value=tarefa_sel.get("Branch", "aguardando"))
                        entra_bu = st.checkbox("Contabilizar esta tarefa nos Gráficos?", value=tarefa_sel.get("Entra_BurnUp", True))

                        if st.form_submit_button("💾 Salvar Alterações"):
                            if "Tarefa" in tarefa_sel or "tarefa" not in tarefa_sel:
                                tarefa_sel["Tarefa"] = novo_titulo
                            else:
                                tarefa_sel["tarefa"] = novo_titulo
                            
                            if "Status" in tarefa_sel or "status" not in tarefa_sel:
                                tarefa_sel["Status"] = novo_status
                            else:
                                tarefa_sel["status"] = novo_status

                            if "SP" in tarefa_sel or "sp" not in tarefa_sel:
                                tarefa_sel["SP"] = novo_sp
                            else:
                                tarefa_sel["sp"] = novo_sp

                            tarefa_sel["Branch"] = nova_branch
                            tarefa_sel["Entra_BurnUp"] = entra_bu
                            salvar_dados_em_disco()
                            st.success("Alterações salvas!")
                            st.rerun()

                with tab_pausar:
                    status_pausa = st.checkbox("Pausar Tarefa", value=tarefa_sel.get("Pausada") or tarefa_sel.get("pausada", False), key=f"chk_p_{nome_sprint}_{idx_t}")
                    just = st.text_area("Justificativa*", value=tarefa_sel.get("Justificativa_Pausa") or tarefa_sel.get("justificativa_pausa", ""), key=f"txt_j_{nome_sprint}_{idx_t}")
                    
                    if st.button("Salvar Pausa", key=f"btn_p_{nome_sprint}_{idx_t}"):
                        if status_pausa:
                            if not just.strip():
                                st.error("⚠️ Justificativa é obrigatória para pausar a tarefa!")
                            else:
                                tarefa_sel["Pausada"] = True
                                tarefa_sel["Justificativa_Pausa"] = just.strip()
                                salvar_dados_em_disco()
                                st.success("Tarefa pausada com sucesso!")
                                st.rerun()
                        else:
                            tarefa_sel["Pausada"] = False
                            tarefa_sel["Justificativa_Pausa"] = ""
                            salvar_dados_em_disco()
                            st.success("Pausa removida! Tarefa reativada.")
                            st.rerun()

                with tab_tags:
                    tags_atuais_norm = [str(t).strip().lower() for t in tarefa_sel.get("Tags", [])]
                    
                    is_imp = "impedimento" in tags_atuais_norm
                    is_inf = any(t in tags_atuais_norm for t in ["aguardando informações", "aguardando informacao"])

                    has_imp = st.checkbox("TAG: Impedimento", value=is_imp, key=f"tag_imp_{nome_sprint}_{idx_t}")
                    has_inf = st.checkbox("TAG: Aguardando Informações", value=is_inf, key=f"tag_inf_{nome_sprint}_{idx_t}")
                    
                    motivo_tag_atual = tarefa_sel.get("Motivo_TAG", "")
                    motivo_input = ""
                    
                    if has_imp or has_inf:
                        motivo_input = st.text_area(
                            "Informe o motivo do Impedimento / Aguardando Informação*",
                            value=motivo_tag_atual,
                            key=f"txt_motivo_tag_{nome_sprint}_{idx_t}",
                            placeholder="Descreva os detalhes do bloqueio ou informação faltante..."
                        )

                    if st.button("Salvar TAGs", key=f"btn_tags_{nome_sprint}_{idx_t}"):
                        novas = []
                        if has_imp:
                            novas.append("Impedimento")
                        if has_inf:
                            novas.append("Aguardando Informações")
                        
                        st.session_state.sprints[nome_sprint]["tarefas"][idx_t]["Tags"] = novas
                        st.session_state.sprints[nome_sprint]["tarefas"][idx_t]["Motivo_TAG"] = motivo_input.strip() if (has_imp or has_inf) else ""
                        
                        salvar_dados_em_disco()
                        st.success("TAGs salvas com sucesso!")
                        st.rerun()

                with tab_excluir:
                    if st.button("🚨 Confirmar Exclusão", key=f"btn_del_{nome_sprint}_{idx_t}", type="primary"):
                        dados_sprint["tarefas"].pop(idx_t)
                        salvar_dados_em_disco()
                        st.success("Tarefa excluída!")
                        st.rerun()

        # --- TABELA INTERATIVA ---
        st.subheader("📋 Tarefas da Sprint")
        st.caption("💡 *Marque ou desmarque a caixa **'Conta nos Gráficos?'** para definir em tempo real quais tarefas compõem a meta.*")

        dados_exibicao = []
        for t in dados_sprint["tarefas"]:
            dados_exibicao.append({
                "ID": t.get("ID") or t.get("id"),
                "Status": t.get("Status") or t.get("status"),
                "Entra_BurnUp": t.get("Entra_BurnUp", True),
                "SP": t.get("SP") or t.get("sp", 5),
                "Tarefa": t.get("Tarefa") or t.get("tarefa", ""),
                "Branch": t.get("Branch", "aguardando"),
                "Profissional": t.get("Profissional") or t.get("profissional", "-"),
                "Pausada": t.get("Pausada") or t.get("pausada", False),
                "Justificativa_Pausa": t.get("Justificativa_Pausa") or t.get("justificativa_pausa", "")
            })

        df_t = pd.DataFrame(dados_exibicao)
        if not df_t.empty:
            df_t_exibicao = df_t[["ID", "Status", "Entra_BurnUp", "SP", "Tarefa", "Branch", "Profissional", "Pausada", "Justificativa_Pausa"]]

            df_editado_tabela = st.data_editor(
                df_t_exibicao,
                key=f"editor_tarefas_check_{nome_sprint}",
                disabled=["ID", "Status", "SP", "Tarefa", "Branch", "Profissional", "Pausada", "Justificativa_Pausa"],
                column_config={
                    "Entra_BurnUp": st.column_config.CheckboxColumn(
                        "Conta nos Gráficos?",
                        help="Marque para incluir no gráfico da Sprint",
                        default=True,
                    ),
                    "Tarefa": st.column_config.TextColumn("Tarefa", width="large")
                },
                use_container_width=True,
                hide_index=True
            )

            mudou = False
            for _, row in df_editado_tabela.iterrows():
                tid_str = str(row["ID"])
                novo_val = bool(row["Entra_BurnUp"])
                for t_item in dados_sprint["tarefas"]:
                    t_id_atual = str(t_item.get("ID") or t_item.get("id"))
                    if t_id_atual == tid_str:
                        if t_item.get("Entra_BurnUp") != novo_val:
                            t_item["Entra_BurnUp"] = novo_val
                            mudou = True
                        break
            
            if mudou:
                salvar_dados_em_disco()
                st.rerun()

        # --- LANÇAMENTO DIÁRIO ---
        st.subheader("📝 Evolução Diária por Tarefa (% Concluído)")
        colunas_dias = [f"Dia {i} ({dias_uteis_lista[i-1].strftime('%d/%m')})" for i in range(1, total_dias_sprint + 1)]

        matriz = []
        for t in dados_sprint["tarefas"]:
            row = {
                "ID": t.get("ID") or t.get("id"), 
                "Tarefa": t.get("Tarefa") or t.get("tarefa", ""), 
                "Status": t.get("Status") or t.get("status", ""),
                "SP": t.get("SP") or t.get("sp", 5),
                "Pausada": t.get("Pausada") or t.get("pausada", False),
                "Entra_BurnUp": t.get("Entra_BurnUp", True)
            }
            hist = t.get("historico_diario", {})
            for d in range(1, total_dias_sprint + 1):
                col_name = colunas_dias[d-1]
                val = hist.get(f"Dia {d}", 0)
                row[col_name] = int(val) if (val is not None and str(val).isdigit()) else 0
            matriz.append(row)

        df_m_novo = pd.DataFrame(matriz)
        df_editado = pd.DataFrame()

        if not df_m_novo.empty:
            config_colunas = {
                "Tarefa": st.column_config.TextColumn("Tarefa", width="large")
            }
            for col in colunas_dias:
                config_colunas[col] = st.column_config.NumberColumn(
                    col,
                    min_value=0,
                    max_value=100,
                    step=5,
                    default=0,
                    format="%d%%"
                )

            df_editado = st.data_editor(
                df_m_novo,
                disabled=["ID", "Tarefa", "Status", "SP", "Pausada", "Entra_BurnUp"],
                hide_index=True,
                use_container_width=True,
                column_config=config_colunas,
                key=f"editor_dias_{nome_sprint}"
            )

            alterado = False
            for _, row in df_editado.iterrows():
                tid_str = str(row["ID"])
                target_task = None
                for t_item in dados_sprint["tarefas"]:
                    t_id_atual = str(t_item.get("ID") or t_item.get("id"))
                    if t_id_atual == tid_str:
                        target_task = t_item
                        break
                
                if target_task is not None:
                    for d in range(1, total_dias_sprint + 1):
                        col_nome = colunas_dias[d-1]
                        raw_val = row[col_nome]
                        try:
                            novo_val = int(raw_val) if pd.notna(raw_val) else 0
                        except (ValueError, TypeError):
                            novo_val = 0
                        
                        if target_task["historico_diario"].get(f"Dia {d}") != novo_val:
                            target_task["historico_diario"][f"Dia {d}"] = novo_val
                            alterado = True

            if alterado:
                salvar_dados_em_disco()
                st.rerun()
        else:
            st.info("ℹ️ Nenhuma tarefa cadastrada nesta Sprint para exibir a evolução diária.")

        # --- GRÁFICOS ---
        if not df_editado.empty:
            tarefas_validas = [t for t in dados_sprint["tarefas"] if t.get("Entra_BurnUp", True) and not (t.get("Pausada") or t.get("pausada", False))]
            fig_sprint = go.Figure()

            ultimo_dia_com_lancamento = 0
            for idx, c in enumerate(colunas_dias):
                tem_lancamento = any(
                    int(r[c]) > 0 
                    for _, r in df_editado.iterrows() 
                    if r["Entra_BurnUp"] and not r["Pausada"] and not pd.isna(r[c])
                )
                if tem_lancamento:
                    ultimo_dia_com_lancamento = idx + 1

            soma_realizada = []
            for idx in range(total_dias_sprint):
                c = colunas_dias[idx]
                
                if idx >= ultimo_dia_com_lancamento:
                    soma_realizada.append(None)
                else:
                    soma_pct = sum(
                        int(r[c]) 
                        for _, r in df_editado.iterrows() 
                        if r["Entra_BurnUp"] and not r["Pausada"] and not pd.isna(r[c])
                    )
                    soma_realizada.append(soma_pct)

            total_sp_sprint = len(tarefas_validas) * 100

            if tipo_sprint == "Evolução":
                linha_estimada = [total_sp_sprint - (i * (total_sp_sprint / total_dias_sprint)) for i in range(total_dias_sprint)]
                linha_realizada = [
                    total_sp_sprint - pct if pct is not None else None 
                    for pct in soma_realizada
                ]
                
                titulo_grafico = f"📉 Gráfico Burn-Down — {nome_sprint}"
                nome_meta = "Meta (Burn-Down)"
                nome_real = "Restante (Real)"
            else:
                linha_estimada = [i * (total_sp_sprint / total_dias_sprint) for i in range(1, total_dias_sprint + 1)]
                linha_realizada = soma_realizada
                
                titulo_grafico = f"📈 Gráfico Burn-Up — {nome_sprint}"
                nome_meta = "Linha Ideal (Meta)"
                nome_real = "Concluído (Real)"

            fig_sprint.add_trace(go.Scatter(
                x=colunas_dias, 
                y=linha_estimada, 
                mode='lines+markers', 
                name=nome_meta, 
                line=dict(color='#0A2540', width=3)
            ))

            fig_sprint.add_trace(go.Scatter(
                x=colunas_dias, 
                y=linha_realizada, 
                mode='lines+markers', 
                name=nome_real, 
                connectgaps=False,
                line=dict(color='#2ca02c', width=3, dash='dash')
            ))

            fig_sprint.update_layout(
                title=dict(
                    text=f"<b>{titulo_grafico}</b>", 
                    font=dict(
                        family="'Segoe UI', Tahoma, Geneva, Verdana, sans-serif", 
                        size=22
                    ),
                    x=0,
                    xanchor='left'
                ),
                template="plotly_white", 
                height=420,
                xaxis_title="Dias Úteis",
                yaxis_title="Progresso Pontuado",
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
            )
            
            st.plotly_chart(fig_sprint, use_container_width=True)
            st.session_state[f"fig_sprint_{nome_sprint}"] = fig_sprint

        # --- GRÁFICO DE ACOMPANHAMENTO DO PROFISSIONAL ---
        prof_data = []
        for t in dados_sprint["tarefas"]:
            prof = t.get("Profissional") or t.get("profissional", "-")
            sp_val = int(t.get("SP") or t.get("sp", 5))
            prof_data.append({"Profissional": prof, "SP Planejado": sp_val})

        df_prof = pd.DataFrame(prof_data)
        if not df_prof.empty:
            df_prof_grouped = df_prof.groupby("Profissional").sum().reset_index()

            def definir_cor_profissional(nome_prof):
                nome_limpo = str(nome_prof).strip().upper()
                termos_genericos = ["CSOL", "DEV", "PRO", "TIME", "AGUARDANDO", "-", "SEM ALOCAÇÃO", "N/A"]
                
                if any(termo in nome_limpo for termo in termos_genericos) or len(nome_limpo.split()) < 2:
                    return '#D9534F'
                return '#0A2540'

            df_prof_grouped["Cor"] = df_prof_grouped["Profissional"].apply(definir_cor_profissional)

            fig_devs = px.bar(
                df_prof_grouped, 
                y="Profissional", 
                x="SP Planejado", 
                orientation="h",
                text="SP Planejado"
            )
            
            fig_devs.update_traces(
                marker_color=df_prof_grouped["Cor"],
                textposition="outside"
            )

            fig_devs.update_layout(
                title=dict(
                    text="<b>👥 Acompanhamento por Atribuição</b>", 
                    font=dict(
                        family="'Segoe UI', Tahoma, Geneva, Verdana, sans-serif", 
                        size=22
                    ),
                    x=0,
                    xanchor='left'
                ),
                template="plotly_white", 
                height=340,
                xaxis_title="Story Points (SP) / Esforço Planejado",
                yaxis_title="Profissional / Responsável"
            )

            st.plotly_chart(fig_devs, use_container_width=True)
            st.session_state[f"fig_devs_{nome_sprint}"] = fig_devs
