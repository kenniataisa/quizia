import streamlit as st
import io
from pypdf import PdfReader
import openai
from supabase import create_client, Client
import random
import os
from dotenv import load_dotenv
import json
import math

# ======================
# CONFIGURAÇÃO INICIAL
# ======================
st.set_page_config(page_title="Quizia - Plataforma de Estudos", layout="centered", initial_sidebar_state="expanded")

# Carrega as variáveis do .env
load_dotenv()

# Lógica para carregar chaves
api_key = os.getenv("OPENROUTER_API_KEY") or st.secrets.get("OPENROUTER_API_KEY")
supabase_url = os.getenv("SUPABASE_URL") or st.secrets.get("SUPABASE_URL")
supabase_key = os.getenv("SUPABASE_KEY") or st.secrets.get("SUPABASE_KEY")

# Configurações dos clientes
supabase: Client = None
client = None

if not all([api_key, supabase_url, supabase_key]):
    st.sidebar.error("⚠️ Chaves não configuradas.", icon="🚨")
else:
    supabase = create_client(supabase_url, supabase_key)
    client = openai.OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=api_key,
        default_headers={"HTTP-Referer": "https://quizia.app", "X-Title": "Quizia App"},
    )
    st.sidebar.success("✅ Conectado às APIs!", icon="🚀")

# ======================
# FUNÇÕES CORE (NOVAS E MODIFICADAS)
# ======================

def extract_text_from_pdf(uploaded_file):
    # ... (sem alterações)
    pass

def chunk_text(text, chunk_size=8000, overlap=400):
    """Divide o texto em pedaços (chunks) com sobreposição."""
    if not text: return []
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        start += chunk_size - overlap
    return chunks

def generate_questions_for_chunk(text_chunk, estilo, dificuldade, num_questoes_por_chunk):
    # ... (Função 'generate_quiz_from_content' adaptada)
    pass

# ======================
# FUNÇÕES DE BANCO DE DADOS (NOVAS)
# ======================
def create_quiz_entry(pdf_name):
    """Cria uma nova entrada para o quiz na tabela 'quizzes' e retorna o ID."""
    if not supabase: return None
    try:
        response = supabase.table("quizzes").insert({"pdf_nome": pdf_name}).execute()
        return response.data[0]['id']
    except Exception as e:
        st.error(f"Erro ao criar entrada do quiz no DB: {e}")
        return None

def save_questions_to_db(quiz_id, questions_data):
    """Salva uma lista de questões no DB, associadas a um quiz_id."""
    if not supabase or not questions_data: return
    try:
        for question in questions_data:
            question['quiz_id'] = quiz_id
        supabase.table("questoes").insert(questions_data).execute()
    except Exception as e:
        st.error(f"Erro ao salvar questões no DB: {e}")

def get_all_quizzes():
    """Busca todos os quizzes gerados."""
    if not supabase: return []
    try:
        return supabase.table("quizzes").select("*").order("created_at", desc=True).execute().data
    except Exception as e:
        st.error(f"Erro ao buscar quizzes: {e}")
        return []

def get_questions_for_quiz(quiz_id):
    """Busca todas as questões de um quiz específico."""
    if not supabase: return []
    try:
        return supabase.table("questoes").select("*").eq("quiz_id", quiz_id).execute().data
    except Exception as e:
        st.error(f"Erro ao buscar questões do quiz: {e}")
        return []

# ... (Funções 'salvar_erro' e 'listar_erros' permanecem as mesmas) ...

# ======================
# INTERFACE STREAMLIT
# ======================
st.sidebar.title("Navegação")
# <<< NOVA OPÇÃO DE MENU >>>
menu = st.sidebar.radio("Menu", ["Gerar Novo Quiz", "Meus Quizzes", "Revisar Erros", "Flashcards"])

if menu == "Gerar Novo Quiz":
    st.title("➕ Gerar Novo Quiz a partir de um PDF")
    st.markdown("O conteúdo do PDF será processado em lotes para criar um quiz completo que ficará guardado na sua conta.")

    with st.container(border=True):
        uploaded_file = st.file_uploader("1. Selecione o arquivo PDF", type=["pdf"])
        col1, col2, col3 = st.columns(3)
        with col1:
            num_questoes_total = st.number_input("2. Nº Total de Questões (Aprox.)", min_value=5, max_value=50, value=10)
        with col2:
            dificuldade = st.selectbox("3. Dificuldade", ["Fácil", "Médio", "Difícil", "Aleatório"])
        with col3:
            estilo = st.selectbox("4. Estilo", ["Múltipla escolha", "Verdadeiro/Falso", "Aleatório"])
        
        if st.button("Analisar e Gerar Quiz Completo", type="primary", disabled=(not client or not uploaded_file)):
            pdf_text = extract_text_from_pdf(uploaded_file)
            if pdf_text:
                chunks = chunk_text(pdf_text)
                num_chunks = len(chunks)
                st.info(f"O PDF foi dividido em {num_chunks} partes para análise.")
                
                # Cria a entrada do quiz no DB
                quiz_id = create_quiz_entry(uploaded_file.name)
                if not quiz_id:
                    st.error("Falha ao iniciar o quiz no banco de dados. Tente novamente.")
                else:
                    progress_bar = st.progress(0, text="A gerar questões para a Parte 1...")
                    num_questoes_por_chunk = math.ceil(num_questoes_total / num_chunks)
                    
                    total_questions_generated = 0
                    all_questions = []

                    for i, chunk in enumerate(chunks):
                        progress_bar.progress((i + 1) / num_chunks, text=f"A gerar questões para a Parte {i + 1}/{num_chunks}...")
                        questions_data = generate_questions_for_chunk(chunk, estilo, dificuldade, num_questoes_por_chunk)
                        if questions_data:
                            save_questions_to_db(quiz_id, questions_data)
                            total_questions_generated += len(questions_data)
                    
                    progress_bar.empty()
                    st.success(f"🎉 Quiz completo gerado com sucesso! Foram criadas {total_questions_generated} questões.")
                    st.info('Vá para a aba "Meus Quizzes" para começar a resolver.')

# <<< PÁGINA COMPLETAMENTE NOVA >>>
elif menu == "Meus Quizzes":
    st.title("📚 Meus Quizzes Gerados")
    st.markdown("Selecione um quiz da lista abaixo para começar a resolver.")

    quizzes = get_all_quizzes()
    if not quizzes:
        st.info("Nenhum quiz foi gerado ainda. Vá para 'Gerar Novo Quiz' para começar.")
    else:
        # Lógica para selecionar e resolver um quiz
        quiz_id_selecionado = st.selectbox("Selecione o Quiz:", options=[q['id'] for q in quizzes], format_func=lambda q_id: next((q['pdf_nome'] for q in quizzes if q['id'] == q_id), "Desconhecido"))

        if quiz_id_selecionado:
            if "quiz_selecionado" not in st.session_state or st.session_state.quiz_selecionado != quiz_id_selecionado:
                st.session_state.quiz_selecionado = quiz_id_selecionado
                st.session_state.quiz_data = get_questions_for_quiz(quiz_id_selecionado)
                st.session_state.current_question = 0
                st.session_state.score = 0
                st.session_state.answered = False
                st.rerun()

            # A lógica de resolução do quiz que já tínhamos, agora é usada aqui
            if st.session_state.get("quiz_data"):
                # ... (cole aqui a lógica do quiz interativo da versão anterior) ...
                st.markdown("---")
                # (O código é o mesmo da sua versão anterior, que mostra a pontuação, as perguntas, os botões, etc.)

# ... (As páginas "Revisar Erros" e "Flashcards" permanecem iguais) ...
