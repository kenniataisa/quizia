import streamlit as st
import io
from pypdf import PdfReader
import openai
from supabase import create_client
import random
import os
from dotenv import load_dotenv
import json

# ======================
# CONFIGURAÇÃO INICIAL
# ======================
st.set_page_config(page_title="Quizia - Quiz Interativo com IA", layout="centered", initial_sidebar_state="expanded")

# Carrega as variáveis do .env (para desenvolvimento local)
load_dotenv()

# Lógica para carregar chaves (funciona localmente, no Vercel e Streamlit Cloud)
api_key = os.getenv("OPENROUTER_API_KEY") or st.secrets.get("OPENROUTER_API_KEY")
supabase_url = os.getenv("SUPABASE_URL") or st.secrets.get("SUPABASE_URL")
supabase_key = os.getenv("SUPABASE_KEY") or st.secrets.get("SUPABASE_KEY")

# Configurações dos clientes Supabase e OpenRouter
supabase = None
client = None

if not all([api_key, supabase_url, supabase_key]):
    st.sidebar.error("⚠️ Chaves não configuradas.", icon="🚨")
else:
    supabase = create_client(supabase_url, supabase_key)
    client = openai.OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=api_key,
        default_headers={
            "HTTP-Referer": "https://quizia-app.vercel.app",  # Substitua pelo URL do seu app
            "X-Title": "Quizia App",
        },
    )
    st.sidebar.success("✅ Conectado às APIs!", icon="🚀")

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

def generate_quiz_from_content(text, estilo, dificuldade, num_questoes):
    """Gera um quiz em formato JSON estruturado."""
    if not client:
        st.error("Cliente da API não configurado.")
        return None

    estilos_disponiveis = ["Múltipla escolha", "Verdadeiro/Falso"]
    if estilo == "Aleatório":
        estilo = random.choice(estilos_disponiveis)
    
    niveis_disponiveis = ["Fácil", "Médio", "Difícil"]
    if dificuldade == "Aleatório":
        dificuldade = random.choice(niveis_disponiveis)

    json_format_instruction = """
    Responda estritamente no seguinte formato JSON, sem nenhum texto ou formatação adicional fora do JSON.
    O JSON deve conter um único objeto com a chave "questoes", que contém uma lista de objetos de pergunta.
    Cada objeto de pergunta deve conter:
    - "pergunta": (string) O texto da pergunta.
    - "estilo": (string) "multipla_escolha" ou "verdadeiro_falso".
    - "opcoes": (list of strings) Uma lista com as opções. Para 'verdadeiro_falso', use ["Verdadeiro", "Falso"].
    - "resposta_correta": (string) O texto exato da resposta correta.
    - "justificativa": (string) Uma explicação clara do porquê a resposta está correta.
    """

    prompt_final = (
        f"Crie {num_questoes} perguntas no estilo '{estilo}' com nível de dificuldade '{dificuldade}', baseadas no texto fornecido. "
        f"{json_format_instruction}\n\n"
        f"Texto de referência:\n{text[:8000]}"
    )

    try:
        completion = client.chat.completions.create(
            model="deepseek/deepseek-chat-v3.1:free",
            messages=[{"role": "user", "content": prompt_final}],
            response_format={"type": "json_object"},
            timeout=180
        )
        response_content = completion.choices[0].message.content
        if not response_content:
            st.error("A resposta da IA foi vazia. Tente um PDF ou modelo diferente.")
            return None

        response_json = json.loads(response_content)
        quiz_data = response_json.get("questoes")
        
        if not quiz_data or not isinstance(quiz_data, list):
             st.error(f"A IA retornou um JSON, mas não no formato esperado (lista de 'questoes'). Tente novamente.")
             return None

        return quiz_data
        
    except json.JSONDecodeError:
        st.error(f"Erro ao decodificar o JSON da IA. Resposta recebida: {response_content}")
        return None
    except Exception as e:
        st.error(f"Erro inesperado ao chamar a API: {e}")
        return None

# ======================
# BANCO DE ERROS (Supabase)
# ======================
def salvar_erro(pergunta, correta, usuario, estilo, opcoes):
    if not supabase: return
    try:
        opcoes_json = json.dumps(opcoes)
        supabase.table("erros").insert({
            "pergunta": pergunta,
            "resposta_correta": correta,
            "resposta_usuario": usuario,
            "estilo": estilo,
            "opcoes": opcoes_json
        }).execute()
        st.toast("Ops! Erro registado para sua revisão.", icon="💔")
    except Exception as e:
        st.error(f"Erro ao salvar no Supabase: {e}")

def listar_erros():
    if not supabase: return []
    try:
        response = supabase.table("erros").select("*").execute()
        return response.data
    except Exception as e:
        st.error(f"Erro ao listar erros do Supabase: {e}")
        return []

# ======================
# INTERFACE STREAMLIT
# ======================

st.sidebar.title("Navegação")
menu = st.sidebar.radio("Menu", ["Gerar Questões", "Revisar Erros", "Flashcards"])

if menu == "Gerar Questões":
    st.title("📘 Quizia - Quiz Interativo com IA")
    st.markdown("Envie um PDF, personalize seu quiz e teste seus conhecimentos!")

    if "quiz_data" not in st.session_state:
        st.session_state.quiz_data = None
        st.session_state.current_question = 0
        st.session_state.score = 0
        st.session_state.answered = False

    with st.container(border=True):
        uploaded_file = st.file_uploader("1. Selecione o arquivo PDF", type=["pdf"])
        col1, col2, col3 = st.columns(3)
        with col1:
            num_questoes = st.number_input("2. Nº de Questões", min_value=1, max_value=10, value=5)
        with col2:
            dificuldade = st.selectbox("3. Dificuldade", ["Fácil", "Médio", "Difícil", "Aleatório"])
        with col3:
            estilo = st.selectbox("4. Estilo", ["Múltipla escolha", "Verdadeiro/Falso", "Aleatório"])
        
        if st.button("Gerar Quiz!", type="primary", disabled=(not client or not uploaded_file)):
            st.session_state.quiz_data = None
            pdf_text = extract_text_from_pdf(uploaded_file)
            if pdf_text:
                with st.spinner("🧠 A IA está a criar um quiz desafiador para si..."):
                    quiz_data = generate_quiz_from_content(pdf_text, estilo, dificuldade, num_questoes)
                    if quiz_data:
                        st.session_state.quiz_data = quiz_data
                        st.session_state.current_question = 0
                        st.session_state.score = 0
                        st.session_state.answered = False
                        st.rerun()

    if st.session_state.quiz_data:
        st.markdown("---")
        st.subheader(f"Pontuação: {st.session_state.score}/{len(st.session_state.quiz_data)}")
        
        idx = st.session_state.current_question
        total_questions = len(st.session_state.quiz_data)
        
        if idx < total_questions:
            question = st.session_state.quiz_data[idx]
            st.markdown(f"#### Pergunta {idx + 1}/{total_questions}")
            
            with st.form(key=f"question_form_{idx}"):
                user_answer = st.radio(
                    label=question["pergunta"],
                    options=question["opcoes"],
                    index=None
                )
                submitted = st.form_submit_button("Responder")

                if submitted:
                    if user_answer is None:
                        st.warning("Por favor, selecione uma resposta antes de continuar.")
                    else:
                        st.session_state.answered = True
                        is_correct = (user_answer == question["resposta_correta"])

                        if is_correct:
                            st.session_state.score += 1
                            st.success(f"🎉 Correto! {question['justificativa']}")
                        else:
                            st.error(f"❌ Incorreto. A resposta certa era **{question['resposta_correta']}**. {question['justificativa']}")
                            salvar_erro(question["pergunta"], question["resposta_correta"], user_answer, question["estilo"], question["opcoes"])
            
            if st.session_state.answered:
                if st.button("Próxima Pergunta ➡️"):
                    st.session_state.current_question += 1
                    st.session_state.answered = False
                    st.rerun()
        else:
            st.balloons()
            st.success(f"🎉 Quiz Concluído! Sua pontuação final é: {st.session_state.score}/{total_questions}")
            if st.button("Gerar Novo Quiz"):
                st.session_state.quiz_data = None
                st.rerun()

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
        st.info("Nenhum erro registado para usar no modo flashcard.")
    else:
        if 'current_card' not in st.session_state or st.button("Próximo Card 🔄"):
            st.session_state.current_card = random.choice(erros)
            st.session_state.show_answer = False

        card = st.session_state.current_card
        
        with st.container(border=True):
            st.markdown(f"**Pergunta:**\n> {card.get('pergunta', 'N/A')}")
            
            if st.button("Revelar Resposta 💡"):
                st.session_state.show_answer = True
            
            if st.session_state.get('show_answer', False):
                st.success(f"**Resposta:** {card.get('resposta_correta', 'N/A')}")
