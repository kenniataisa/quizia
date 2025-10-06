import streamlit as st
import io
from pypdf import PdfReader
import openai
from supabase import create_client, Client
import random
import os
from dotenv import load_dotenv
import json
import time

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
# FUNÇÕES CORE
# ======================

def extract_text_from_pdf(uploaded_file):
    text = ""
    try:
        uploaded_file.seek(0)
        reader = PdfReader(uploaded_file)
        for page in reader.pages:
            extracted = page.extract_text()
            if extracted:
                text += extracted
        return text
    except Exception as e:
        st.error(f"Erro ao processar o PDF: {e}")
        return None

def chunk_text(text, chunk_size=8000, overlap=400):
    if not text: return []
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        start += chunk_size - overlap
    return chunks

def generate_questions_for_chunk(text_chunk, estilo, dificuldade):
    if not client: return None

    estilos_disponiveis = ["Múltipla escolha", "Verdadeiro/Falso"]
    if estilo == "Aleatório":
        estilo = random.choice(estilos_disponiveis)
    
    niveis_disponiveis = ["Fácil", "Médio", "Difícil"]
    if dificuldade == "Aleatório":
        dificuldade = random.choice(niveis_disponiveis)

    json_format_instruction = """
    Responda estritamente no seguinte formato JSON, contendo um objeto com a chave "questoes", que contém uma lista de objetos de pergunta.
    Cada objeto de pergunta deve ter: "pergunta", "estilo", "opcoes", "resposta_correta", e "justificativa".
    """

    prompt_final = (
        f"Analise o texto fornecido e crie quantas perguntas forem necessárias para cobrir exaustivamente todos os conceitos e informações importantes. "
        f"As perguntas devem ser no estilo '{estilo}' com nível de dificuldade '{dificuldade}'. "
        f"{json_format_instruction}\n\n"
        f"Texto de referência:\n{text_chunk}"
    )

    try:
        completion = client.chat.completions.create(
            model="deepseek/deepseek-chat-v3.1:free",
            messages=[{"role": "user", "content": prompt_final}],
            response_format={"type": "json_object"},
            timeout=180
        )
        response_content = completion.choices[0].message.content
        if not response_content: return None
        response_json = json.loads(response_content)
        return response_json.get("questoes")
    except Exception as e:
        print(f"Erro ao chamar a API para um chunk: {e}")
        return None

# ======================
# FUNÇÕES DE BANCO DE DADOS
# ======================
def create_quiz_entry(pdf_name):
    if not supabase: return None
    try:
        response = supabase.table("quizzes").insert({"pdf_nome": pdf_name}).execute()
        return response.data[0]['id']
    except Exception as e:
        st.error(f"Erro ao criar entrada do quiz no DB: {e}")
        return None

def save_questions_to_db(quiz_id, questions_data):
    if not supabase or not questions_data: return
    try:
        for question in questions_data:
            question['quiz_id'] = quiz_id
        supabase.table("questoes").insert(questions_data).execute()
    except Exception as e:
        st.error(f"Erro ao salvar questões no DB: {e}")

def get_all_quizzes():
    if not supabase: return []
    try:
        return supabase.table("quizzes").select("id, pdf_nome, created_at").order("created_at", desc=True).execute().data
    except Exception as e:
        st.error(f"Erro ao buscar quizzes: {e}")
        return []

def get_questions_for_quiz(quiz_id):
    if not supabase: return []
    try:
        return supabase.table("questoes").select("*").eq("quiz_id", quiz_id).execute().data
    except Exception as e:
        st.error(f"Erro ao buscar questões do quiz: {e}")
        return []

def salvar_erro(pergunta, correta, usuario, estilo, opcoes, justificativa):
    if not supabase: return
    try:
        opcoes_json = json.dumps(opcoes)
        supabase.table("erros").insert({
            "pergunta": pergunta,
            "resposta_correta": correta,
            "resposta_usuario": usuario,
            "estilo": estilo,
            "opcoes": opcoes_json,
            "justificativa": justificativa
        }).execute()
        st.toast("Ops! Erro registado para sua revisão.", icon="💔")
    except Exception as e:
        st.error(f"Erro ao salvar no Supabase: {e}")

def listar_erros():
    if not supabase: return []
    try:
        return supabase.table("erros").select("*").order("created_at", desc=True).execute().data
    except Exception as e:
        st.error(f"Erro ao listar erros do Supabase: {e}")
        return []

# ======================
# INTERFACE STREAMLIT
# ======================
st.sidebar.title("Navegação")
menu = st.sidebar.radio("Menu", ["Gerar Novo Quiz", "Meus Quizzes", "Revisar Erros", "Flashcards"])

if menu == "Gerar Novo Quiz":
    st.title("➕ Gerar Novo Quiz a partir de um PDF")
    st.markdown("O conteúdo do PDF será processado em lotes para criar um quiz completo que ficará guardado na sua conta.")

    with st.container(border=True):
        uploaded_file = st.file_uploader("1. Selecione o arquivo PDF", type=["pdf"])
        col1, col2 = st.columns(2)
        with col1:
            dificuldade = st.selectbox("2. Dificuldade das Questões", ["Fácil", "Médio", "Difícil", "Aleatório"])
        with col2:
            estilo = st.selectbox("3. Estilo das Questões", ["Múltipla escolha", "Verdadeiro/Falso", "Aleatório"])
        
        if st.button("Analisar e Gerar Quiz Completo", type="primary", disabled=(not client or not uploaded_file)):
            with st.status("A iniciar o processo de geração do quiz...", expanded=True) as status:
                try:
                    status.update(label="Passo 1/5: A extrair texto do PDF...", state="running")
                    pdf_text = extract_text_from_pdf(uploaded_file)
                    if not pdf_text:
                        status.update(label="Falha ao extrair texto do PDF.", state="error", expanded=False)
                        st.stop()
                    
                    status.update(label="Passo 2/5: A dividir o conteúdo em partes...", state="running")
                    chunks = chunk_text(pdf_text)
                    num_chunks = len(chunks)
                    st.write(f"Conteúdo dividido em {num_chunks} partes para análise.")
                    
                    status.update(label="Passo 3/5: A criar registo do quiz no banco de dados...", state="running")
                    quiz_id = create_quiz_entry(uploaded_file.name)
                    if not quiz_id:
                        status.update(label="Falha ao registar o quiz no banco de dados.", state="error", expanded=False)
                        st.stop()
                    
                    status.update(label=f"Passo 4/5: A gerar questões com a IA (0/{num_chunks})...", state="running")
                    total_questions_generated = 0
                    for i, chunk in enumerate(chunks):
                        questions_data = generate_questions_for_chunk(chunk, estilo, dificuldade)
                        if questions_data:
                            save_questions_to_db(quiz_id, questions_data)
                            total_questions_generated += len(questions_data)
                        status.update(label=f"Passo 4/5: A gerar questões com a IA ({i + 1}/{num_chunks})... {total_questions_generated} questões criadas.", state="running")
                        time.sleep(1)
                    
                    status.update(label=f"Passo 5/5: A finalizar...", state="running")
                    time.sleep(2)
                    status.update(label=f"Quiz gerado com sucesso! Foram criadas {total_questions_generated} questões.", state="complete", expanded=False)
                    
                    st.success(f"🎉 Quiz '{uploaded_file.name}' está pronto!")
                    st.info('Vá para a aba "Meus Quizzes" para começar a resolver.')

                except Exception as e:
                    status.update(label=f"Ocorreu um erro: {e}", state="error")

elif menu == "Meus Quizzes":
    st.title("📚 Meus Quizzes Gerados")
    st.markdown("Selecione um quiz da lista abaixo para começar a resolver.")

    quizzes = get_all_quizzes()
    if not quizzes:
        st.info("Nenhum quiz foi gerado ainda. Vá para 'Gerar Novo Quiz' para começar.")
    else:
        quiz_options = {q['id']: f"{q['pdf_nome']} (criado em {q['created_at'][:10]})" for q in quizzes}
        quiz_id_selecionado = st.selectbox("Selecione o Quiz:", options=quiz_options.keys(), format_func=lambda q_id: quiz_options[q_id])

        if "quiz_selecionado" not in st.session_state or st.session_state.quiz_selecionado != quiz_id_selecionado:
            st.session_state.quiz_selecionado = quiz_id_selecionado
            st.session_state.quiz_data = get_questions_for_quiz(quiz_id_selecionado)
            st.session_state.current_question = 0
            st.session_state.score = 0
            st.session_state.answered = False
            st.rerun()

        if st.session_state.get("quiz_data"):
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
                                salvar_erro(question["pergunta"], question["resposta_correta"], user_answer, question["estilo"], question["opcoes"], question["justificativa"])
                
                if st.session_state.answered:
                    if st.button("Próxima Pergunta ➡️"):
                        st.session_state.current_question += 1
                        st.session_state.answered = False
                        st.rerun()
            else:
                st.balloons()
                st.success(f"🎉 Quiz Concluído! Sua pontuação final é: {st.session_state.score}/{total_
