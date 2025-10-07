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
import concurrent.futures

# ======================
# CONFIGURAÇÃO INICIAL
# ======================
st.set_page_config(page_title="Quizia - Plataforma de Estudos", layout="centered", initial_sidebar_state="expanded")
load_dotenv()

# --- Carregamento de Chaves ---
api_key = os.getenv("OPENROUTER_API_KEY") or st.secrets.get("OPENROUTER_API_KEY")
supabase_url = os.getenv("SUPABASE_URL") or st.secrets.get("SUPABASE_URL")
supabase_key = os.getenv("SUPABASE_KEY") or st.secrets.get("SUPABASE_KEY")

# --- Verificação e Conexão ---
if not all([api_key, supabase_url, supabase_key]):
    st.error("⚠️ Chaves de API não configuradas. Verifique seu arquivo .env ou os Secrets do Streamlit.", icon="🚨")
    st.stop()

supabase = create_client(supabase_url, supabase_key)
client = openai.OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=api_key,
    default_headers={"HTTP-Referer": "https://quizia.app", "X-Title": "Quizia App"},
)

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
    You must respond strictly in the following JSON format, containing an object with the key "questoes", which holds a list of question objects.
    Each question object must have these keys: "pergunta", "estilo", "opcoes", "resposta_correta", and "justificativa".
    The entire JSON response, including all keys and values (questions, options, justifications), must be in Brazilian Portuguese (pt-BR).
    """

    prompt_final = (
        f"Analyze the provided text and create as many questions as necessary to exhaustively cover all important concepts and information. "
        f"The questions should be in the '{estilo}' style with a '{dificuldade}' difficulty level. "
        f"{json_format_instruction}\n\n"
        f"Reference Text:\n{text_chunk}"
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
# FUNÇÕES DE BANCO DE DADOS (VERSÃO PÚBLICA)
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
        opcoes_json = json.dumps(opcoes, ensure_ascii=False)
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
        st.error(f"Erro ao salvar erro no Supabase: {e}")

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
menu = st.sidebar.radio("Menu", ["Gerar Novo Quiz", "Todos os Quizzes", "Revisar Erros", "Flashcards"])

if menu == "Gerar Novo Quiz":
    st.title("➕ Gerar Novo Quiz a partir de um PDF")
    st.markdown("O conteúdo do PDF será processado em lotes para criar um quiz completo que ficará disponível para todos.")

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
                    
                    status.update(label=f"Passo 4/5: A gerar questões com a IA (0/{num_chunks} processados)...", state="running")
                    all_generated_questions = []
                    with concurrent.futures.ThreadPoolExecutor() as executor:
                        future_to_chunk = {executor.submit(generate_questions_for_chunk, chunk, estilo, dificuldade): chunk for chunk in chunks}
                        for i, future in enumerate(concurrent.futures.as_completed(future_to_chunk)):
                            try:
                                questions_data = future.result()
                                if questions_data:
                                    all_generated_questions.extend(questions_data)
                                status.update(
                                    label=f"Passo 4/5: A gerar questões... ({i + 1}/{num_chunks}) partes processadas. {len(all_generated_questions)} questões criadas.",
                                    state="running"
                                )
                            except Exception as exc:
                                st.warning(f"Um chunk do PDF gerou uma exceção: {exc}")

                    status.update(label="Passo 5/5: A salvar todas as questões no banco de dados...", state="running")
                    if all_generated_questions:
                        save_questions_to_db(quiz_id, all_generated_questions)
                    
                    total_questions_generated = len(all_generated_questions)
                    status.update(label=f"Quiz gerado com sucesso! Foram criadas {total_questions_generated} questões.", state="complete", expanded=False)
                    
                    st.success(f"🎉 Quiz '{uploaded_file.name}' está pronto!")
                    st.info('Vá para a aba "Todos os Quizzes" para começar a resolver.')

                except Exception as e:
                    status.update(label=f"Ocorreu um erro geral no processo: {e}", state="error")

elif menu == "Todos os Quizzes":
    st.title("📚 Todos os Quizzes Gerados")
    st.markdown("Selecione um quiz da lista abaixo para começar a resolver.")

    quizzes = get_all_quizzes()
    if not quizzes:
        st.info("Nenhum quiz foi gerado ainda. Vá para 'Gerar Novo Quiz' para começar.")
    else:
        quiz_options = {q['id']: f"{q.get('pdf_nome', 'Quiz sem nome')} (criado em {q.get('created_at', '')[:10]})" for q in quizzes}
        quiz_id_selecionado = st.selectbox("Selecione o Quiz:", options=list(quiz_options.keys()), format_func=lambda q_id: quiz_options[q_id])

        if quiz_id_selecionado:
            if "quiz_id_selecionado" not in st.session_state or st.session_state.quiz_id_selecionado != quiz_id_selecionado:
                st.session_state.quiz_id_selecionado = quiz_id_selecionado
                st.session_state.quiz_data = get_questions_for_quiz(quiz_id_selecionado)
                st.session_state.current_question = 0
                st.session_state.score = 0
                st.session_state.answered = False
                st.rerun()

            if st.session_state.get("quiz_data"):
                total_questions = len(st.session_state.quiz_data)
                if total_questions == 0:
                    st.warning("Este quiz não contém nenhuma questão. Tente gerá-lo novamente ou selecione outro.")
                else:
                    st.markdown("---")
                    st.subheader(f"Pontuação: {st.session_state.score}/{total_questions}")
                    
                    idx = st.session_state.current_question
                    
                    if idx < total_questions:
                        question = st.session_state.quiz_data[idx]
                        st.markdown(f"#### Pergunta {idx + 1}/{total_questions}")
                        
                        with st.form(key=f"question_form_{idx}"):
                            options = question.get("opcoes", [])
                            if not isinstance(options, list): options = []

                            user_answer = st.radio(
                                label=question["pergunta"],
                                options=options,
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
                                        st.success(f"🎉 Correto! {question.get('justificativa', '')}")
                                    else:
                                        st.error(f"❌ Incorreto. A resposta certa era **{question['resposta_correta']}**. {question.get('justificativa', '')}")
                                        salvar_erro(
                                            question.get("pergunta"), 
                                            question.get("resposta_correta"), 
                                            user_answer, 
                                            question.get("estilo"), 
                                            options, 
                                            question.get("justificativa")
                                        )
                        
                        if st.session_state.get("answered"):
                            if st.button("Próxima Pergunta ➡️"):
                                st.session_state.current_question += 1
                                st.session_state.answered = False
                                st.rerun()
                    else:
                        st.balloons()
                        st.success(f"🎉 Quiz Concluído! Sua pontuação final é: {st.session_state.score}/{total_questions}")

elif menu == "Revisar Erros":
    st.title("🧐 Revise Seus Erros")
    st.markdown("Aqui estão as questões que foram respondidas incorretamente para que possa revisar e aprender.")
    erros = listar_erros()
    if not erros:
        st.info("Nenhuma questão foi registrada como errada ainda. Parabéns!")
    else:
        for i, erro in enumerate(erros):
            with st.container(border=True):
                st.markdown(f"**Pergunta:** {erro['pergunta']}")
                st.markdown(f"**Sua resposta:** <span style='color:red;'>{erro['resposta_usuario']}</span>", unsafe_allow_html=True)
                st.markdown(f"**Resposta correta:** <span style='color:green;'>{erro['resposta_correta']}</span>", unsafe_allow_html=True)
                if erro.get('justificativa'):
                    st.info(f"**Justificativa:** {erro['justificativa']}")

elif menu == "Flashcards":
    st.title("🗂️ Flashcards para Estudo")
    st.info("Funcionalidade de Flashcards em desenvolvimento. Volte em breve!")
