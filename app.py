import streamlit as st
import fitz  # PyMuPDF
import io
import base64
from PIL import Image
import openai
from supabase import create_client
import random
import os
from dotenv import load_dotenv

# ======================
# CONFIGURAÇÃO INICIAL (VERSÃO FINAL E SEGURA)
# ======================
st.set_page_config(page_title="Gerador de Questões Multimodal", layout="wide")

# Carrega as variáveis do .env se o arquivo existir (para desenvolvimento local)
load_dotenv()

# Verifica se está rodando no Streamlit Cloud ou localmente para carregar as chaves
if 'OPENROUTER_API_KEY' in st.secrets:
    # Ambiente de produção (Streamlit Cloud)
    OPENROUTER_API_KEY = st.secrets["OPENROUTER_API_KEY"]
    SUPABASE_URL = st.secrets["SUPABASE_URL"]
    SUPABASE_KEY = st.secrets["SUPABASE_KEY"]
    st.sidebar.success("🔑 Chaves de produção carregadas!", icon="✅")
else:
    # Ambiente de desenvolvimento (local)
    OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
    SUPABASE_URL = os.getenv("SUPABASE_URL")
    SUPABASE_KEY = os.getenv("SUPABASE_KEY")
    st.sidebar.info("🔑 Chaves de desenvolvimento carregadas do .env", icon="👩‍💻")

# Configurações Supabase
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# Configurações OpenRouter
client = None
if not OPENROUTER_API_KEY or not SUPABASE_URL or not SUPABASE_KEY:
    st.sidebar.error("⚠️ Chaves não configuradas. Verifique o .env ou os Secrets.", icon="🚨")
else:
    client = openai.OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=OPENROUTER_API_KEY,
        default_headers={
            "HTTP-Referer": "<YOUR_SITE_URL>", # Opcional: Substitua pela URL do seu app
            "X-Title": "Gerador de Questões Multimodal", # Opcional: Nome do seu app
        },
    )

# ======================
# FUNÇÕES CORE
# ======================

def extract_text_and_images(uploaded_file, max_images=3):
    """Extrai texto e até N imagens do PDF"""
    try:
        uploaded_file.seek(0)
        raw = uploaded_file.read()
        pdf_document = fitz.open(stream=io.BytesIO(raw), filetype="pdf")
        
        text = ""
        images_bytes = []

        for page in pdf_document:
            text += page.get_text()
            if len(images_bytes) < max_images:
                for img_info in page.get_images(full=True):
                    xref = img_info[0]
                    base_image = pdf_document.extract_image(xref)
                    img_bytes = base_image["image"]
                    images_bytes.append(img_bytes)
                    if len(images_bytes) >= max_images:
                        break
        
        return text, images_bytes
    except Exception as e:
        st.error(f"Erro ao processar o PDF: {e}")
        return None, None


def generate_questions_multimodal(text, images_bytes, estilo):
    """Gera questões multimodais (texto + imagens) usando o modelo Qwen"""
    if not client:
        st.error("Cliente da API não configurado. Verifique suas chaves.")
        return None

    prompts = {
        "Múltipla escolha": "Crie 5 perguntas de múltipla escolha com 4 alternativas cada (A, B, C, D), baseadas neste texto e nas imagens. Indique claramente qual é a alternativa correta para cada pergunta.",
        "Verdadeiro/Falso": "Crie 5 afirmações e indique se são verdadeiras ou falsas com base no texto e nas imagens, justificando cada resposta.",
        "Dissertativa": "Crie 5 perguntas abertas que exijam interpretação crítica do texto e das imagens.",
        "Flashcards": "Crie pares de pergunta/resposta curtas e diretas, no estilo flashcard, com base nas informações mais importantes do texto e das imagens."
    }
    prompt = prompts.get(estilo, "Crie 5 perguntas com base no texto e nas imagens.")

    content = [{"type": "text", "text": f"{prompt}\n\nTexto extraído:\n{text[:4000]}"}]

    for idx, img_bytes in enumerate(images_bytes):
        base64_image = base64.b64encode(img_bytes).decode("utf-8")
        content.append({"type": "text", "text": f"Análise da Imagem {idx+1}:"})
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}})

    try:
        completion = client.chat.completions.create(
            model="qwen/qwen2.5-vl-72b-instruct:free",
            messages=[{"role": "user", "content": content}],
            timeout=120  # Aumentado para modelos maiores
        )
        return completion.choices[0].message.content
    except Exception as e:
        st.error(f"Erro ao chamar a API: {e}")
        return None

# ======================
# BANCO DE ERROS (Supabase)
# ======================

def salvar_erro(pergunta, correta, usuario, opcoes, estilo):
    try:
        supabase.table("erros").insert({
            "pergunta": pergunta,
            "resposta_correta": correta,
            "resposta_usuario": usuario,
            "opcoes": opcoes,
            "estilo": estilo
        }).execute()
    except Exception as e:
        st.error(f"Erro ao salvar no Supabase: {e}")


def listar_erros():
    try:
        response = supabase.table("erros").select("*").execute()
        return response.data
    except Exception as e:
        st.error(f"Erro ao listar erros do Supabase: {e}")
        return []

# ======================
# INTERFACE STREAMLIT
# ======================

# --- Menu Lateral ---
menu = st.sidebar.radio("Menu", ["Gerar Questões", "Revisar Erros", "Flashcards"])

# --- Lógica das Páginas ---
if menu == "Gerar Questões":
    st.title("📘 Gerador de Questões Multimodal")
    st.markdown("Envie um arquivo PDF para extrair texto e imagens e gerar um quiz personalizado.")

    uploaded_file = st.file_uploader("Selecione o arquivo PDF", type=["pdf"])
    estilo = st.selectbox("Escolha o estilo das questões:",
                          ["Múltipla escolha", "Verdadeiro/Falso", "Dissertativa", "Flashcards"])
    
    # Inicializa o estado da sessão para armazenar os resultados
    if 'questions_generated' not in st.session_state:
        st.session_state.questions_generated = ""

    if st.button("Gerar Questões", disabled=(not client or not uploaded_file), type="primary"):
        pdf_text, pdf_images = extract_text_and_images(uploaded_file)
        
        if pdf_text is not None:
            st.success("✅ PDF processado com sucesso!")
            
            with st.expander("Pré-visualização do Conteúdo Extraído"):
                st.text_area("Texto extraído (primeiros 1500 caracteres)", pdf_text[:1500], height=200)
                if pdf_images:
                    st.image([Image.open(io.BytesIO(img)) for img in pdf_images],
                             caption=[f"Imagem {i+1}" for i in range(len(pdf_images))], width=150)

            with st.spinner("🧠 A IA está pensando... Isso pode levar um ou dois minutos."):
                result = generate_questions_multimodal(pdf_text, pdf_images, estilo)
                if result:
                    st.session_state.questions_generated = result
                else:
                    st.error("Não foi possível gerar as questões.")
    
    if st.session_state.questions_generated:
        st.markdown("---")
        st.markdown("### ✨ Questões Geradas")
        st.markdown(st.session_state.questions_generated)


elif menu == "Revisar Erros":
    st.title("📂 Histórico de Erros")
    st.markdown("Revise as questões que você errou para fortalecer seu aprendizado.")
    erros = listar_erros()
    if erros:
        for i, e in enumerate(erros):
            with st.container(border=True):
                st.markdown(f"**{i+1}. Pergunta:** {e.get('pergunta', 'N/A')}")
                st.error(f"Sua resposta: {e.get('resposta_usuario', 'N/A')}")
                st.success(f"Resposta correta: {e.get('resposta_correta', 'N/A')}")
    else:
        st.info("Você ainda não registrou nenhum erro. Continue praticando!")


elif menu == "Flashcards":
    st.title("🃏 Modo Flashcards")
    st.markdown("Teste seu conhecimento com base nas questões que você errou anteriormente.")
    
    erros = listar_erros()
    if not erros:
        st.info("Nenhum erro registrado para usar no modo flashcard.")
    else:
        # Usar o estado da sessão para não trocar de card a cada interação
        if 'current_card' not in st.session_state or st.button("Próximo Card 🔄"):
            st.session_state.current_card = random.choice(erros)
            st.session_state.show_answer = False

        card = st.session_state.current_card
        
        with st.container(border=True):
            st.markdown(f"**Pergunta:**\n> {card.get('pergunta', 'N/A')}")
            
            if st.button("Revelar Resposta 💡"):
                st.session_state.show_answer = True
            
            if st.session_state.show_answer:
                st.success(f"**Resposta:** {card.get('resposta_correta', 'N/A')}")

