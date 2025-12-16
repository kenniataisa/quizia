# ============================================================
# QUIZIA - VERSÃO COM CONTROLE PEDAGÓGICO E PAGINAÇÃO
# ============================================================

import streamlit as st
import base64
import fitz
import json
import time
import re
from supabase import create_client, Client
from openai import OpenAI

# ------------------------------------------------------------
# CONFIGURAÇÕES
# ------------------------------------------------------------
SUPABASE_URL = st.secrets["SUPABASE_URL"]
SUPABASE_KEY = st.secrets["SUPABASE_KEY"]
OPENROUTER_API_KEY = st.secrets["OPENROUTER_API_KEY"]

MODELO_VISAO = "nvidia/nemotron-nano-12b-v2-vl:free"
MODELO_TEXTO = "meta-llama/llama-3.3-70b-instruct:free"

SITE_URL = "http://quizia.streamlit.app"
SITE_NAME = "QuizIA App"

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ------------------------------------------------------------
# CLIENTE OPENROUTER
# ------------------------------------------------------------
def create_openrouter_client():
    return OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=OPENROUTER_API_KEY
    )

client_ai = create_openrouter_client()

HEADERS = {
    "HTTP-Referer": SITE_URL,
    "X-Title": SITE_NAME
}

# ------------------------------------------------------------
# EXTRAÇÃO PDF
# ------------------------------------------------------------
def extract_content_from_pdf(uploaded_file):
    doc = fitz.open(stream=uploaded_file.read(), filetype="pdf")
    pages = []

    for i, page in enumerate(doc):
        text = page.get_text()
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
        img_b64 = base64.b64encode(pix.tobytes("png")).decode()

        pages.append({
            "page": i + 1,
            "text": text,
            "images": [f"data:image/png;base64,{img_b64}"]
        })

    return pages

# ------------------------------------------------------------
# LIMPEZA JSON
# ------------------------------------------------------------
def limpar_json_ia(content):
    content = re.sub(r"```json|```", "", content)
    match = re.search(r"\[.*\]", content, re.DOTALL)
    if not match:
        return []
    try:
        return json.loads(match.group())
    except:
        return []

# ------------------------------------------------------------
# FILTRO PEDAGÓGICO (ANTI QUESTÃO SEM SENTIDO)
# ------------------------------------------------------------
def questao_pedagogica(q):
    blacklist = [
        "cor", "cores", "layout", "design",
        "estilo visual", "formatação", "fonte"
    ]
    texto = q["pergunta"].lower()
    return not any(b in texto for b in blacklist)

# ------------------------------------------------------------
# GERAÇÃO DE QUESTÕES (VISÃO CONTROLADA)
# ------------------------------------------------------------
def gerar_questoes(pagina, dificuldade, estilo):
    prompt = f"""
MISSÃO:
Analise o texto e a imagem da página e gere questões PEDAGÓGICAS.

REGRAS OBRIGATÓRIAS:
- Só crie perguntas visuais se houver:
  • gráficos com dados
  • tabelas
  • fórmulas
  • diagramas explicativos
- NÃO crie perguntas sobre:
  • cores decorativas
  • layout
  • estilo visual
  • design gráfico

REGRA DE OURO:
Se a pergunta não puder ser respondida estudando o CONTEÚDO, NÃO CRIE.

CONTEÚDO:
{pagina["text"]}

FORMATO JSON (APENAS):
[
  {{
    "pergunta": "...",
    "opcoes": ["A)...", "B)..."],
    "resposta_correta": "...",
    "trecho_referencia": "Trecho literal do PDF",
    "pagina": {pagina["page"]},
    "tipo": "multipla_escolha"
  }}
]
"""

    messages = [{"type": "text", "text": prompt}]
    for img in pagina["images"]:
        messages.append({"type": "image_url", "image_url": {"url": img}})

    response = client_ai.chat.completions.create(
        model=MODELO_VISAO,
        messages=[{"role": "user", "content": messages}],
        extra_headers=HEADERS
    )

    raw = limpar_json_ia(response.choices[0].message.content)
    return [q for q in raw if questao_pedagogica(q)]

# ------------------------------------------------------------
# INTERFACE DE QUIZ (1 QUESTÃO POR VEZ)
# ------------------------------------------------------------
def render_quiz(questoes):
    if "questao_atual" not in st.session_state:
        st.session_state.questao_atual = 0

    if "banco_erros" not in st.session_state:
        st.session_state.banco_erros = []

    i = st.session_state.questao_atual
    q = questoes[i]

    st.markdown(f"### Questão {i+1}/{len(questoes)}")
    st.markdown(q["pergunta"])

    resposta = st.radio(
        "Escolha:",
        q["opcoes"],
        key=f"resp_{i}",
        index=None
    )

    if resposta:
        correta = q["resposta_correta"]
        acertou = resposta.split(")")[0] == correta.split(")")[0]

        if acertou:
            st.success("✅ Correto!")
        else:
            st.error("❌ Errado")
            st.info(f"✔️ Correta: {correta}")
            st.caption(f"📖 Página {q['pagina']} — {q['trecho_referencia']}")

            st.session_state.banco_erros.append({
                "pergunta": q["pergunta"],
                "sua": resposta,
                "correta": correta,
                "fonte": q["trecho_referencia"],
                "pagina": q["pagina"]
            })

    col1, col2 = st.columns(2)
    with col1:
        if st.button("⬅️ Anterior") and i > 0:
            st.session_state.questao_atual -= 1
            st.rerun()
    with col2:
        if st.button("➡️ Próxima") and i < len(questoes) - 1:
            st.session_state.questao_atual += 1
            st.rerun()

# ------------------------------------------------------------
# BANCO DE ERROS
# ------------------------------------------------------------
def render_banco_erros():
    st.header("❌ Banco de Erros")

    if not st.session_state.banco_erros:
        st.success("Nenhum erro até agora 🎉")
        return

    for i, e in enumerate(st.session_state.banco_erros):
        st.markdown(f"**{i+1}. {e['pergunta']}**")
        st.error(f"Sua resposta: {e['sua']}")
        st.success(f"Correta: {e['correta']}")
        st.caption(f"📄 Página {e['pagina']} — {e['fonte']}")
        st.markdown("---")

# ------------------------------------------------------------
# MAIN
# ------------------------------------------------------------
st.set_page_config("QuizIA", layout="centered")

if "page" not in st.session_state:
    st.session_state.page = "home"

with st.sidebar:
    if st.button("🏠 Criar Quiz"):
        st.session_state.page = "home"
    if st.button("❌ Banco de Erros"):
        st.session_state.page = "erros"

if st.session_state.page == "erros":
    render_banco_erros()
else:
    st.title("🧠 QuizIA – Aprendizado Real")

    pdf = st.file_uploader("Envie o PDF", type="pdf")
    if pdf and st.button("🚀 Gerar Quiz"):
        paginas = extract_content_from_pdf(pdf)
        questoes = []
        for p in paginas:
            questoes.extend(gerar_questoes(p, "Padrão", "Misto"))

        if questoes:
            st.session_state.questoes = questoes
            st.session_state.questao_atual = 0
            st.rerun()

    if "questoes" in st.session_state:
        render_quiz(st.session_state.questoes)
