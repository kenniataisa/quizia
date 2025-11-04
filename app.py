
# ==========================================================
# ðŸ§  QUIZIA PRO+ - VersÃ£o Completa com Supabase e IA
# ==========================================================
# Autor: Kennia Taisa
# Data: 2025
# DescriÃ§Ã£o:
#   Aplicativo em Streamlit que gera, avalia e armazena quizzes a partir de PDFs,
#   com integraÃ§Ã£o ao Supabase e modelos de IA (DeepSeek + Gemma).
# ==========================================================

import streamlit as st
import io
from pypdf import PdfReader
import openai
from supabase import create_client, Client
import random
import os
from dotenv import load_dotenv
import json
import concurrent.futures

# ======================
# CONFIGURAÃ‡ÃƒO INICIAL
# ======================
st.set_page_config(page_title="QuizIA Pro+", layout="wide", initial_sidebar_state="expanded")
load_dotenv()

api_key = os.getenv("OPENROUTER_API_KEY") or st.secrets.get("OPENROUTER_API_KEY")
supabase_url = os.getenv("SUPABASE_URL") or st.secrets.get("SUPABASE_URL")
supabase_key = os.getenv("SUPABASE_KEY") or st.secrets.get("SUPABASE_KEY")

if not all([api_key, supabase_url, supabase_key]):
    st.error("Chaves de API nao configuradas.", icon="🔐")
    st.stop()

supabase = create_client(supabase_url, supabase_key)
client = openai.OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=api_key,
    default_headers={"HTTP-Referer": "https://quizia.app", "X-Title": "Quizia App"},
)

# --- MODELOS DE IA (GeraÃ§Ã£o e AvaliaÃ§Ã£o) ---
MODELO_GERACAO = "tngtech/deepseek-r1t2-chimera:free"
MODELO_AVALIACAO = "google/gemma-3-27b-it:free"

# ==========================================================
# FUNÃ‡Ã•ES SUPABASE (CONFIGURAÃ‡ÃƒO DO BANCO)
# ==========================================================
# Tabelas necessÃ¡rias no Supabase:
# 1ï¸âƒ£ questoes -> id, nome_quiz, disciplina, estilo, pergunta, opcoes, resposta_correta, justificativa, contexto_citado, dificuldade
# 2ï¸âƒ£ erros -> id, pergunta, resposta_correta, resposta_usuario, estilo, justificativa, contexto_citado, created_at
# ==========================================================

def salvar_questoes_no_supabase(nome_quiz, disciplina, questoes):
    try:
        for q in questoes:
            data = {
                "nome_quiz": nome_quiz,
                "disciplina": disciplina,
                "estilo": q.get("estilo"),
                "pergunta": q.get("pergunta") or q.get("texto_base") or q.get("pergunta_guia"),
                "opcoes": json.dumps(q.get("opcoes", []), ensure_ascii=False),
                "resposta_correta": q.get("resposta_correta") or ", ".join(q.get("respostas_aceitaveis", [])),
                "justificativa": q.get("justificativa", ""),
                "contexto_citado": q.get("contexto_citado", ""),
                "dificuldade": q.get("dificuldade", "Desconhecida")
            }
            supabase.table("questoes").insert(data).execute()
        st.success(f"Questoes do quiz '{nome_quiz}' salvas na disciplina '{disciplina}' com sucesso!")
    except Exception as e:
        st.error(f"Erro ao salvar questÃµes: {e}")

def listar_disciplinas():
    try:
        data = supabase.table("questoes").select("disciplina").execute().data
        if data:
            return sorted(list(set([d["disciplina"] for d in data if d["disciplina"]])))
        return []
    except Exception as e:
        st.error(f"Erro ao listar disciplinas: {e}")
        return []

def listar_questoes_por_disciplina(disciplina):
    try:
        return supabase.table("questoes").select("*").eq("disciplina", disciplina).execute().data
    except Exception as e:
        st.error(f"Erro ao buscar questÃµes: {e}")
        return []

# ==========================================================
# INTERFACE INICIAL
# ==========================================================
st.title("QuizIA Pro+")
st.markdown("Plataforma de geracao inteligente de quizzes com IA")

if "show_upload" not in st.session_state:
    st.session_state.show_upload = False

if st.button("ðŸ“¤ Fazer upload de PDF para gerar quiz"):
    st.session_state.show_upload = not st.session_state.show_upload

if st.session_state.show_upload:
    uploaded_file = st.file_uploader("Selecione um arquivo PDF", type=["pdf"])
    if uploaded_file:
        st.success("Arquivo carregado com sucesso! Vá até o menu lateral para configurar e gerar suas questoes.")

# ==========================================================
# MENU LATERAL
# ==========================================================
st.sidebar.title("Navegacao")
menu = st.sidebar.radio(
    "Escolha uma opcao:",
    ["Disciplinas", "Flashcards", "Revisao de Erros", "Configurar Estilos", "Configurar Dificuldade"]
)

# ----------------------------------------------------------
# 1ï¸âƒ£ MENU DISCIPLINAS
# ----------------------------------------------------------
if menu == "Disciplinas":
    disciplinas = listar_disciplinas()
    if not disciplinas:
        st.info("Nenhuma disciplina cadastrada ainda. Gere um quiz primeiro!")
    else:
        disciplina = st.selectbox("Selecione uma disciplina:", disciplinas)
        questoes = listar_questoes_por_disciplina(disciplina)
        if questoes:
            for q in questoes:
                with st.container(border=True):
                    st.markdown(f"**Pergunta:** {q['pergunta']}")
                    if q.get("opcoes"):
                        opcoes = json.loads(q["opcoes"])
                        st.write("**Opcoes:**", ", ".join(opcoes))
                    st.write(f"**Resposta Correta:** {q['resposta_correta']}")
                    st.write(f"**Justificativa:** {q['justificativa']}")
                    st.caption(f"**Dificuldade:** {q['dificuldade']}")

# ----------------------------------------------------------
# 2ï¸âƒ£ MENU CONFIGURAR ESTILOS
# ----------------------------------------------------------
elif menu == "Configurar Estilos":
    st.header("Estilos de Questoes")
    estilos = st.multiselect(
        "Selecione os estilos de questoes que deseja permitir:",
        ["Multipla Escolha", "Aberta", "Preencher Lacuna", "Associar Colunas", "Verdadeiro ou Falso"],
        default=["Multipla Escolha", "Aberta"]
    )
    st.session_state.estilos_selecionados = estilos
    st.success("Estilos atualizados com sucesso!")

# ----------------------------------------------------------
# 3ï¸âƒ£ MENU CONFIGURAR DIFICULDADE
# ----------------------------------------------------------
elif menu == "Configurar Dificuldade":
    st.header("Niveis de Dificuldade")
    dificuldade = st.selectbox("Escolha o nivel de dificuldade:", ["Aleatorio", "Facil", "Medio", "Dificil"])
    st.session_state.dificuldade = dificuldade
    st.success("Nivel de dificuldade configurado!")

# ----------------------------------------------------------
# 4ï¸âƒ£ MENU FLASHCARDS
# ----------------------------------------------------------
elif menu == "Flashcards":
    st.header("Flashcards")
    st.info("Funcionalidade em desenvolvimento.")

# ----------------------------------------------------------
# 5ï¸âƒ£ MENU REVISÃƒO DE ERROS
# ----------------------------------------------------------
elif menu == "Revisao de Erros":
    st.header("Revisao de Erros")
    erros = supabase.table("erros").select("*").order("created_at", desc=True).execute().data
    if not erros:
        st.info("Nenhum erro registrado ainda.")
    else:
        for erro in erros:
            with st.container(border=True):
                st.write(f"**Pergunta:** {erro['pergunta']}")
                st.write(f"**Sua Resposta:** {erro['resposta_usuario']}")
                st.write(f"**Correta:** {erro['resposta_correta']}")
                st.caption(erro.get("justificativa", ""))
