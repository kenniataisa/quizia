import streamlit as st
import io
from pypdf import PdfReader
import openai
from supabase import create_client
import random
import os
from dotenv import load_dotenv

# ======================
# CONFIGURAÇÃO INICIAL (VERSÃO FINAL E SEGURA)
# ======================
st.set_page_config(page_title="Gerador de Questões - Quizia", layout="wide")

# Carrega as variáveis do .env se o arquivo existir (para desenvolvimento local)
load_dotenv()

# Verifica se está rodando no Streamlit Cloud, Vercel ou localmente
api_key = None
supabase_url = None
supabase_key = None

if 'OPENROUTER_API_KEY' in st.secrets:
    # Ambiente Streamlit Cloud
    api_key = st.secrets["OPENROUTER_API_KEY"]
    supabase_url = st.secrets["SUPABASE_URL"]
    supabase_key = st.secrets["SUPABASE_KEY"]
    st.sidebar.success("🔑 Chaves de produção (Streamlit) carregadas!", icon="✅")
else:
    # Ambiente Vercel ou Local
    api_key = os.getenv("OPENROUTER_API_KEY")
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_KEY")
    if api_key:
        st.sidebar.info("🔑 Chaves de ambiente (Vercel/Local) carregadas.", icon="⚙️")

# Configurações Supabase e OpenRouter
supabase = None
client = None

if not all([api_key, supabase_url, supabase_key]):
    st.sidebar.error("⚠️ Chaves não configuradas. Verifique o .env ou as Environment Variables.", icon="🚨")
else:
    supabase = create_client(supabase_url, supabase_key)
    client = openai.OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=api_key,
        default_headers={
            "HTTP-Referer": "https://quizia-app.vercel.app", # Exemplo
            "X-Title": "Quizia App",
        },
    )

# ======================
# FUNÇÕES CORE
# ======================

def extract_text_from_pdf(uploaded_file):
    """Extrai apenas texto do PDF usando pypdf."""
    try:
        uploaded_file.seek(0)
        reader = PdfReader(uploaded_file)
        text = ""
        for page in reader.pages:
            extracted = page.extract_text()
            if extracted:
                text += extracted
        return text
    except Exception as e:
        st.error(f"Erro ao processar o PDF: {e}")
        return None

def generate_questions(text, estilo):
    """Gera questões com base apenas em texto."""
    if not client:
        st.error("Cliente da API não configurado.")
        return None

    prompts = {
        "Múltipla escolha": "Crie 5 perguntas de múltipla escolha com 4 alternativas cada (A, B, C, D), baseadas neste texto. Indique claramente qual é a alternativa correta para cada pergunta.",
        "Verdadeiro/Falso": "Crie 5 afirmações e indique se são verdadeiras ou falsas com base no texto, justificando cada resposta.",
        "Dissertativa": "Crie 5 perguntas abertas que exijam interpretação crítica do texto.",
        "Flashcards": "Crie pares de pergunta/resposta curtas e diretas, no estilo flashcard, com base nas informações mais importantes do texto."
    }
    prompt = prompts.get(estilo, "Crie 5 perguntas com base no texto.")

    try:
        completion = client.chat.completions.create(
            model="deepseek/deepseek-chat-v3.1:free",
            messages=[{"role": "user", "content": f"{prompt}\n\nTexto extraído:\n{text[:8000]}"}],
            timeout=120
        )
        return completion.choices[0].message.content
    except Exception as e:
        st.error(f"Erro ao chamar a API: {e}")
        return None

# ======================
# BANCO DE ERROS (Supabase) - COMPLETO
# ======================

def salvar_erro(pergunta, correta, usuario, opcoes, estilo):
    """Salva um erro no banco de dados Supabase."""
    if not supabase:
        st.error("Conexão com o banco de dados não configurada.")
        return
    try:
        supabase.table("erros").insert({
            "pergunta": pergunta,
            "resposta_correta": correta,
            "resposta_usuario": usuario,
            "opcoes": opcoes,
            "estilo": estilo
        }).execute()
        st.toast("Erro registrado para revisão futura!")
    except Exception as e:
        st.error(f"Erro ao salvar no Supabase: {e}")


def listar_erros():
    """Lista todos os erros do banco de dados Supabase."""
    if not supabase:
        st.error("Conexão com o banco de dados não configurada.")
        return []
    try:
        response = supabase.table("erros").select("*").execute()
        return response.data
    except Exception as e:
        st.error(f"Erro ao listar erros do Supabase: {e}")
        return []

# ======================
# INTERFACE STREAMLIT - COMPLETA
# ======================

# --- Menu Lateral ---
menu = st.sidebar.radio("Menu", ["Gerar Questões", "Revisar Erros", "Flashcards"])

# --- Página: Gerar Questões ---
if menu == "Gerar Questões":
    st.title("📘 Quizia - Gerador de Questões a partir de PDFs")
    st.markdown("Envie um arquivo PDF para extrair o texto e gerar um quiz personalizado.")

    uploaded_file = st.file_uploader("Selecione o arquivo PDF", type=["pdf"])
    estilo = st.selectbox("Escolha o estilo das questões:",
                          ["Múltipla escolha", "Verdadeiro/Falso", "Dissertativa", "Flashcards"])

    if 'questions_generated' not in st.session_state:
        st.session_state.questions_generated = ""

    if st.button("Gerar Questões", disabled=(not client or not uploaded_file), type="primary"):
        pdf_text = extract_text_from_pdf(uploaded_file)
        
        if pdf_text:
            st.success("✅ PDF processado com sucesso!")
            with st.expander("Pré-visualização do Texto Extraído"):
                st.text_area("Texto (primeiros 1500 caracteres)", pdf_text[:1500], height=200)

            with st.spinner("🧠 A IA está pensando... Isso pode levar um momento."):
                result = generate_questions(pdf_text, estilo)
                if result:
                    st.session_state.questions_generated = result
                else:
                    st.error("Não foi possível gerar as questões.")
    
    if st.session_state.questions_generated:
        st.markdown("---")
        st.markdown("### ✨ Questões Geradas")
        st.markdown(st.session_state.questions_generated)

# --- Página: Revisar Erros ---
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

# --- Página: Flashcards ---
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
            st.session_state.show_answer = False # Reseta a visibilidade da resposta

        card = st.session_state.current_card
        
        with st.container(border=True):
            st.markdown(f"**Pergunta:**\n> {card.get('pergunta', 'N/A')}")
            
            # Botão para revelar a resposta
            if st.button("Revelar Resposta 💡"):
                st.session_state.show_answer = True
            
            # Mostra a resposta se o botão foi clicado
            if st.session_state.get('show_answer', False):
                st.success(f"**Resposta:** {card.get('resposta_correta', 'N/A')}")

