import streamlit as st
import fitz  # PyMuPDF
import json
from supabase import create_client, Client
from openai import OpenAI
import time
import uuid

# -------------------------------
# 🔑 Configurações
# -------------------------------
SUPABASE_URL = st.secrets["SUPABASE_URL"]
SUPABASE_KEY = st.secrets["SUPABASE_KEY"]
OPENROUTER_API_KEY = st.secrets["OPENROUTER_API_KEY"]

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# -------------------------------
# 🔧 Inicializa clientes OpenRouter
# -------------------------------
def create_openrouter_client():
    return OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=OPENROUTER_API_KEY,
    )

deepseek_client = create_openrouter_client()
llama_client = create_openrouter_client()

# -------------------------------
# 📘 Função: Extrair texto do PDF
# -------------------------------
def extract_text_from_pdf(uploaded_file):
    doc = fitz.open(stream=uploaded_file.read(), filetype="pdf")
    text = ""
    for page in doc:
        text += page.get_text("text") + "\n"
    return text.strip()

# -------------------------------
# ✂️ Dividir texto em chunks
# -------------------------------
def chunk_text(text, max_chars=3000):
    paragraphs = text.split("\n")
    chunks = []
    current_chunk = ""
    for para in paragraphs:
        if len(current_chunk) + len(para) < max_chars:
            current_chunk += para + "\n"
        else:
            chunks.append(current_chunk.strip())
            current_chunk = para + "\n"
    if current_chunk:
        chunks.append(current_chunk.strip())
    return chunks

# -------------------------------
# 🤖 Gerar questões com DeepSeek
# -------------------------------
def gerar_questoes_deepseek(texto):
    prompt = f"""
    Gere 5 questões de múltipla escolha baseadas no seguinte conteúdo:
    {texto}

    Formato de resposta em JSON:
    [
      {{
        "pergunta": "texto da questão",
        "opcoes": ["A) ...", "B) ...", "C) ...", "D) ..."],
        "resposta_correta": "A",
        "justificativa": "explicação curta baseada no texto"
      }}
    ]
    """
    response = deepseek_client.chat.completions.create(
        model="tngtech/deepseek-r1t2-chimera:free",
        messages=[{"role": "user", "content": prompt}],
    )
    content = response.choices[0].message.content
    try:
        return json.loads(content)
    except:
        st.warning("Não foi possível decodificar a resposta da IA. Verifique o formato.")
        return []

# -------------------------------
# 🧹 Refinar questões com Llama
# -------------------------------
def refinar_questoes_llama(questoes):
    prompt = f"""
    Revise as seguintes questões, corrija inconsistências e melhore clareza e gramática.
    Mantenha o formato JSON idêntico.

    Questões:
    {json.dumps(questoes, ensure_ascii=False, indent=2)}
    """
    response = llama_client.chat.completions.create(
        model="meta-llama/llama-3.3-70b-instruct:free",
        messages=[{"role": "user", "content": prompt}],
    )
    content = response.choices[0].message.content
    try:
        return json.loads(content)
    except:
        return questoes

# -------------------------------
# 💾 Salvar no Supabase
# -------------------------------
def salvar_quiz(disciplina, nome, questoes):
    data = {
        "id": str(uuid.uuid4()),
        "nome": nome,
        "disciplina": disciplina,
        "questoes": questoes,
    }
    supabase.table("quizzes").insert(data).execute()

# -------------------------------
# 🧩 Interface Streamlit
# -------------------------------
st.set_page_config(page_title="QuizIA", layout="wide")
st.title("🧠 QuizIA - Gerador de Questões com DeepSeek + Llama")

aba = st.sidebar.radio("Navegar", ["Gerar Quiz", "Responder Quiz"])

# -------------------------------
# 📄 A. Gerar Quiz
# -------------------------------
if aba == "Gerar Quiz":
    st.header("📘 Enviar conteúdo para gerar questões")

    uploaded_file = st.file_uploader("Envie um PDF", type=["pdf"])
    mostrar_texto = st.checkbox("Mostrar campo de texto manual")
    texto_manual = ""

    if mostrar_texto:
        texto_manual = st.text_area("Ou cole o conteúdo aqui", height=200)

    disciplina = st.text_input("Disciplina")
    nome_quiz = st.text_input("Nome do Quiz")

    if st.button("🚀 Gerar Questões"):
        with st.spinner("Gerando questões com IA..."):
            texto = ""
            if uploaded_file:
                texto = extract_text_from_pdf(uploaded_file)
            elif texto_manual:
                texto = texto_manual
            else:
                st.warning("Envie um PDF ou insira texto!")
                st.stop()

            chunks = chunk_text(texto)
            questoes_final = []

            for i, chunk in enumerate(chunks):
                st.info(f"🔹 Processando parte {i+1}/{len(chunks)}...")
                q = gerar_questoes_deepseek(chunk)
                q_refinado = refinar_questoes_llama(q)
                questoes_final.extend(q_refinado)
                time.sleep(2)

            if questoes_final:
                salvar_quiz(disciplina, nome_quiz, questoes_final)
                st.success(f"✅ {len(questoes_final)} questões geradas e salvas com sucesso!")
                st.json(questoes_final)

# -------------------------------
# 🎯 B. Responder Quiz
# -------------------------------
elif aba == "Responder Quiz":
    st.header("🎯 Responder um Quiz")

    quizzes = supabase.table("quizzes").select("*").execute()
    if not quizzes.data:
        st.warning("Nenhum quiz encontrado.")
    else:
        nomes = [q["nome"] for q in quizzes.data]
        escolha = st.selectbox("Escolha um quiz", nomes)
        quiz = next(q for q in quizzes.data if q["nome"] == escolha)

        questoes = quiz["questoes"]
        if isinstance(questoes, str):
            questoes = json.loads(questoes)

        for i, q in enumerate(questoes):
            st.write(f"**{i+1}. {q['pergunta']}**")
            resposta = st.radio("Escolha uma opção:", q["opcoes"], key=f"q{i}")
            if st.button(f"Verificar {i+1}", key=f"b{i}"):
                correta = q["resposta_correta"]
                if resposta.startswith(correta):
                    st.success("✅ Correto!")
                else:
                    st.error(f"❌ Incorreto. Resposta correta: {correta}")
                st.info(q["justificativa"])
