# ==========================================================
# 🤖 QUIZIA PRO+ - Versão Completa com Supabase e IA (com Avaliação)
# ==========================================================
# Autor: Kennia Taisa
# Descrição:
#   Geração e avaliação automática de quizzes baseados em PDFs,
#   integrando DeepSeek e Gemma via OpenRouter + Supabase.
# ==========================================================

import streamlit as st
from pypdf import PdfReader
import openai
from supabase import create_client
import os
from dotenv import load_dotenv
import json

# ======================
# CONFIGURAÇÃO INICIAL
# ======================
st.set_page_config(page_title="QuizIA Pro+", layout="wide", initial_sidebar_state="expanded")
load_dotenv()

api_key = os.getenv("OPENROUTER_API_KEY") or st.secrets.get("OPENROUTER_API_KEY")
supabase_url = os.getenv("SUPABASE_URL") or st.secrets.get("SUPABASE_URL")
supabase_key = os.getenv("SUPABASE_KEY") or st.secrets.get("SUPABASE_KEY")

if not all([api_key, supabase_url, supabase_key]):
    st.error("⚠️ Chaves de API não configuradas corretamente.", icon="🔐")
    st.stop()

# --- Inicializa conexões ---
supabase = create_client(supabase_url, supabase_key)
client = openai.OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=api_key,
    default_headers={"HTTP-Referer": "https://quizia.app", "X-Title": "Quizia App"},
)

# --- Modelos IA ---
MODELO_GERACAO = "tngtech/deepseek-r1t2-chimera:free"
MODELO_AVALIACAO = "google/gemma-3-27b-it:free"

# ==========================================================
# FUNÇÕES AUXILIARES
# ==========================================================
def gerar_questoes(texto, disciplina):
    """Gera questões usando DeepSeek"""
    prompt = f"""
    Gere 5 questões objetivas de {disciplina} com 4 alternativas.
    Retorne APENAS um JSON válido no formato:
    [
      {{
        "pergunta": "...",
        "opcoes": ["A) ...", "B) ...", "C) ...", "D) ..."],
        "resposta_correta": "A",
        "justificativa": "..."
      }}
    ]
    Texto base:
    {texto[:4000]}
    """
    resposta = client.chat.completions.create(
        model=MODELO_GERACAO,
        messages=[{"role": "user", "content": prompt}]
    )
    return json.loads(resposta.choices[0].message.content)


def avaliar_resposta(pergunta, resposta_usuario, resposta_correta, texto):
    """Avalia a resposta com Gemma e extrai o trecho do livro"""
    prompt = f"""
    Você é um corretor de quizzes. Avalie a seguinte resposta e diga se está certa ou errada,
    e cite o trecho mais relevante do texto original que a justifique.

    Pergunta: {pergunta}
    Resposta do aluno: {resposta_usuario}
    Resposta correta: {resposta_correta}

    Texto de referência:
    {texto[:3000]}

    Responda em JSON:
    {{
      "correto": true/false,
      "comentario": "...",
      "trecho_justificativo": "..."
    }}
    """
    r = client.chat.completions.create(
        model=MODELO_AVALIACAO,
        messages=[{"role": "user", "content": prompt}]
    )
    try:
        return json.loads(r.choices[0].message.content)
    except:
        return {"correto": False, "comentario": "Erro ao processar IA.", "trecho_justificativo": ""}


def salvar_questoes_no_supabase(nome_quiz, disciplina, questoes):
    """Salva o quiz no Supabase"""
    data = {"nome": nome_quiz, "disciplina": disciplina, "questoes": json.dumps(questoes, ensure_ascii=False)}
    supabase.table("quizzes").insert(data).execute()


def salvar_erro(pergunta, resposta_usuario, resposta_correta, justificativa):
    """Armazena erros para revisão posterior"""
    try:
        data = {
            "pergunta": pergunta,
            "resposta_usuario": resposta_usuario,
            "resposta_correta": resposta_correta,
            "justificativa": justificativa,
        }
        supabase.table("erros").insert(data).execute()
    except Exception:
        pass


def listar_disciplinas():
    data = supabase.table("quizzes").select("disciplina").execute().data
    return sorted(list(set([d["disciplina"] for d in data if d["disciplina"]])))


def listar_questoes_por_disciplina(disciplina):
    registros = supabase.table("quizzes").select("*").eq("disciplina", disciplina).execute().data
    questoes = []
    for r in registros:
        try:
            qlist = json.loads(r["questoes"])
            questoes.extend(qlist if isinstance(qlist, list) else [qlist])
        except:
            pass
    return questoes


# ==========================================================
# INTERFACE
# ==========================================================
st.title("🤖 QuizIA Pro+")
st.markdown("**Plataforma de geração e avaliação automática de quizzes com IA**")


menu = st.sidebar.radio(
    "📚 Navegação",
    ["Gerar Quiz", "Responder Quiz", "Revisão de Erros", "Configurar Estilos", "Configurar Dificuldade"]
)

# ----------------------------------------------------------
# GERAR QUIZ
# ----------------------------------------------------------
if menu == "Gerar Quiz":
    st.header("📄 Gerar Quiz com IA")
    pdf = st.file_uploader("Selecione um PDF", type=["pdf"])
    nome_quiz = st.text_input("📝 Nome do Quiz")
    disciplina = st.text_input("📘 Disciplina")

    if pdf and nome_quiz and disciplina and st.button("🚀 Gerar Questões"):
        reader = PdfReader(pdf)
        texto = "\n".join([p.extract_text() for p in reader.pages if p.extract_text()])
        questoes = gerar_questoes(texto, disciplina)
        salvar_questoes_no_supabase(nome_quiz, disciplina, questoes)
        st.success(f"{len(questoes)} questões geradas com sucesso!")

# ----------------------------------------------------------
# RESPONDER QUIZ
# ----------------------------------------------------------
elif menu == "Responder Quiz":
    st.header("🧠 Responder Quiz")
    disciplinas = listar_disciplinas()
    if not disciplinas:
        st.info("Nenhuma disciplina disponível ainda.")
    else:
        disciplina = st.selectbox("Escolha uma disciplina:", disciplinas)
        questoes = listar_questoes_por_disciplina(disciplina)
        if not questoes:
            st.warning("Nenhuma questão cadastrada para esta disciplina.")
        else:
            pdf_texto = ""
            respostas_usuario = {}
            for q in questoes:
                st.subheader(q["pergunta"])
                resposta = st.radio("Escolha sua resposta:", q["opcoes"], key=q["pergunta"])
                respostas_usuario[q["pergunta"]] = resposta

            if st.button("Verificar Respostas"):
                with st.spinner("Avaliando suas respostas..."):
                    for q in questoes:
                        resultado = avaliar_resposta(
                            q["pergunta"],
                            respostas_usuario[q["pergunta"]],
                            q["resposta_correta"],
                            q["justificativa"],
                        )
                        if resultado["correto"]:
                            st.success(f"✅ {q['pergunta']}")
                        else:
                            st.error(f"❌ {q['pergunta']}")
                            st.write(f"💬 **Comentário:** {resultado['comentario']}")
                            st.caption(f"📘 Trecho do texto: {resultado['trecho_justificativo']}")
                            salvar_erro(q["pergunta"], respostas_usuario[q["pergunta"]], q["resposta_correta"], resultado["comentario"])

# ----------------------------------------------------------
# REVISÃO DE ERROS
# ----------------------------------------------------------
elif menu == "Revisão de Erros":
    st.header("📋 Revisão de Erros")
    try:
        erros = supabase.table("erros").select("*").order("created_at", desc=True).execute().data
        if not erros:
            st.info("Nenhum erro registrado ainda.")
        else:
            for e in erros:
                with st.expander(e["pergunta"]):
                    st.write(f"**Sua resposta:** {e['resposta_usuario']}")
                    st.write(f"**Correta:** {e['resposta_correta']}")
                    st.caption(e["justificativa"])
    except Exception:
        st.warning("Tabela 'erros' ainda não configurada.")
