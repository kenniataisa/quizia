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

api_key = os.getenv("OPENROUTER_API_KEY") or st.secrets.get("OPENROUTER_API_KEY")
supabase_url = os.getenv("SUPABASE_URL") or st.secrets.get("SUPABASE_URL")
supabase_key = os.getenv("SUPABASE_KEY") or st.secrets.get("SUPABASE_KEY")

if not all([api_key, supabase_url, supabase_key]):
    st.error("⚠️ Chaves de API não configuradas.", icon="🚨")
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
            if extracted: text += extracted
        return text
    except Exception as e:
        st.error(f"Erro ao processar o PDF: {e}"); return None

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
    if estilo == "Aleatório": estilo = random.choice(estilos_disponiveis)
    niveis_disponiveis = ["Fácil", "Médio", "Difícil"]
    if dificuldade == "Aleatório": dificuldade = random.choice(niveis_disponiveis)
    json_format_instruction = """
    You must respond strictly in the following JSON format, containing an object with the key "questoes", which holds a list of question objects.
    Each question object must have these keys: "pergunta", "estilo", "opcoes", "resposta_correta", and "justificativa".
    The entire JSON response, including all keys and values, must be in Brazilian Portuguese (pt-BR).
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
        return json.loads(response_content).get("questoes")
    except Exception as e:
        print(f"Erro ao chamar a API para um chunk: {e}"); return None

# ======================
# FUNÇÕES DE BANCO DE DADOS
# ======================
def create_quiz_entry(pdf_name):
    if not supabase: return None
    try:
        return supabase.table("quizzes").insert({"pdf_nome": pdf_name}).execute().data[0]['id']
    except Exception as e:
        st.error(f"Erro ao criar entrada do quiz no DB: {e}"); return None

def save_questions_to_db(quiz_id, questions_data):
    if not supabase or not questions_data: return False
    try:
        for question in questions_data:
            question['quiz_id'] = quiz_id
        supabase.table("questoes").insert(questions_data).execute()
        return True
    except Exception as e:
        st.error(f"Erro ao salvar questões no DB: {e}"); return False

def get_all_quizzes():
    if not supabase: return []
    try:
        return supabase.table("quizzes").select("id, pdf_nome, created_at").order("created_at", desc=True).execute().data
    except Exception as e:
        st.error(f"Erro ao buscar quizzes: {e}"); return []

def get_questions_for_quiz(quiz_id):
    if not supabase: return []
    try:
        return supabase.table("questoes").select("*").eq("quiz_id", quiz_id).execute().data
    except Exception as e:
        st.error(f"Erro ao buscar questões do quiz: {e}"); return []

def delete_quiz(quiz_id):
    if not supabase: return False
    try:
        supabase.table("quizzes").delete().eq("id", quiz_id).execute()
        return True
    except Exception as e:
        st.error(f"Erro ao apagar o quiz: {e}"); return False

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
        st.error(f"Erro ao listar erros do Supabase: {e}"); return []

# ======================
# INTERFACE STREAMLIT
# ======================
st.sidebar.title("Navegação")
menu = st.sidebar.radio("Menu", ["Gerar Novo Quiz", "Todos os Quizzes", "Revisar Erros", "Flashcards"])

if menu == "Gerar Novo Quiz":
    st.title("➕ Gerar Novo Quiz a partir de um PDF")
    st.markdown("O conteúdo do PDF será processado para criar um quiz completo.")

    with st.container(border=True):
        uploaded_file = st.file_uploader("1. Selecione o arquivo PDF", type=["pdf"])
        quiz_name = st.text_input("2. Dê um nome para o seu Quiz", value=uploaded_file.name if uploaded_file else "")
        col1, col2 = st.columns(2)
        with col1: dificuldade = st.selectbox("3. Dificuldade", ["Fácil", "Médio", "Difícil", "Aleatório"])
        with col2: estilo = st.selectbox("4. Estilo", ["Múltipla escolha", "Verdadeiro/Falso", "Aleatório"])
        
        if st.button("Analisar e Gerar Quiz", type="primary", disabled=(not all([client, uploaded_file, quiz_name]))):
            # --- LÓGICA DE GERAÇÃO QUE ESTAVA FALTANDO ---
            with st.status("A iniciar o processo de geração do quiz...", expanded=True) as status:
                try:
                    status.update(label="Passo 1/5: Extraindo texto do PDF...", state="running")
                    pdf_text = extract_text_from_pdf(uploaded_file)
                    if not pdf_text:
                        status.update(label="Falha ao extrair texto.", state="error", expanded=False); st.stop()
                    
                    status.update(label="Passo 2/5: Dividindo o conteúdo...", state="running")
                    chunks = chunk_text(pdf_text)
                    num_chunks = len(chunks)
                    st.write(f"Conteúdo dividido em {num_chunks} partes para análise.")
                    
                    status.update(label="Passo 3/5: Criando registo do quiz...", state="running")
                    quiz_id = create_quiz_entry(quiz_name)
                    if not quiz_id:
                        status.update(label="Falha ao registar o quiz.", state="error", expanded=False); st.stop()
                    
                    status.update(label=f"Passo 4/5: Gerando questões com a IA (0/{num_chunks})...", state="running")
                    all_generated_questions = []
                    with concurrent.futures.ThreadPoolExecutor() as executor:
                        future_to_chunk = {executor.submit(generate_questions_for_chunk, chunk, estilo, dificuldade): chunk for chunk in chunks}
                        for i, future in enumerate(concurrent.futures.as_completed(future_to_chunk)):
                            try:
                                questions_data = future.result()
                                if questions_data:
                                    all_generated_questions.extend(questions_data)
                                status.update(
                                    label=f"Passo 4/5: Gerando questões... ({i + 1}/{num_chunks}) partes processadas. {len(all_generated_questions)} questões criadas.",
                                    state="running"
                                )
                            except Exception as exc:
                                st.warning(f"Um chunk do PDF gerou uma exceção: {exc}")

                    status.update(label="Passo 5/5: Salvando questões no banco de dados...", state="running")
                    if all_generated_questions:
                        success = save_questions_to_db(quiz_id, all_generated_questions)
                        if not success:
                           status.update(label="Falha ao salvar questões no banco de dados.", state="error", expanded=False); st.stop()
                    
                    total_questions_generated = len(all_generated_questions)
                    status.update(label=f"Quiz gerado! Foram criadas {total_questions_generated} questões.", state="complete", expanded=False)
                    
                    st.success(f"🎉 Quiz '{quiz_name}' está pronto!")
                    st.info('Vá para a aba "Todos os Quizzes" para começar a resolver.')

                except Exception as e:
                    status.update(label=f"Ocorreu um erro geral: {e}", state="error")

elif menu == "Todos os Quizzes":
    st.title("📚 Todos os Quizzes Gerados")
    st.markdown("Selecione um quiz para resolver ou apagar.")

    quizzes = get_all_quizzes()
    if not quizzes:
        st.info("Nenhum quiz foi gerado ainda.")
    else:
        quiz_options = {q['id']: f"{q.get('pdf_nome', 'Quiz sem nome')} (criado em {q.get('created_at', '')[:10]})" for q in quizzes}
        quiz_id_selecionado = st.selectbox("Selecione o Quiz:", options=list(quiz_options.keys()), format_func=lambda q_id: quiz_options.get(q_id, "Opção inválida"), index=None, placeholder="Escolha um quiz...")

        if quiz_id_selecionado:
            if "confirm_delete" not in st.session_state:
                st.session_state.confirm_delete = False

            col1, col2 = st.columns([3, 1])
            with col2:
                if st.button("Apagar Quiz 🗑️", type="secondary", use_container_width=True):
                    st.session_state.confirm_delete = True
            
            if st.session_state.confirm_delete:
                st.warning(f"**Você tem certeza que quer apagar o quiz '{quiz_options[quiz_id_selecionado]}'?** Esta ação não pode ser desfeita.", icon="⚠️")
                c1, c2 = st.columns(2)
                if c1.button("Sim, apagar permanentemente", type="primary", use_container_width=True):
                    if delete_quiz(quiz_id_selecionado):
                        st.success("Quiz apagado com sucesso!")
                        st.session_state.confirm_delete = False
                        # Limpar o estado para evitar que o quiz apagado continue selecionado
                        st.session_state.quiz_id_selecionado = None
                        st.session_state.quiz_data = None
                        time.sleep(1)
                        st.rerun()
                    else:
                        st.error("Falha ao apagar o quiz.")
                if c2.button("Não, cancelar", use_container_width=True):
                    st.session_state.confirm_delete = False
                    st.rerun()
            
            if not st.session_state.confirm_delete:
                if "quiz_id_selecionado" not in st.session_state or st.session_state.quiz_id_selecionado != quiz_id_selecionado:
                    st.session_state.quiz_id_selecionado = quiz_id_selecionado
                    st.session_state.quiz_data = get_questions_for_quiz(quiz_id_selecionado)
                    st.session_state.current_question = 0
                    st.session_state.score = 0
                    st.session_state.answered = False
                    st.rerun()

                if "quiz_data" in st.session_state and st.session_state.quiz_data:
                    total_questions = len(st.session_state.quiz_data)
                    st.markdown("---")
                    st.subheader(f"Pontuação: {st.session_state.score}/{total_questions}")
                    
                    idx = st.session_state.current_question
                    if idx < total_questions:
                        question = st.session_state.quiz_data[idx]
                        with st.form(key=f"question_form_{idx}"):
                            options = question.get("opcoes", [])
                            if not isinstance(options, list): options = []
                            user_answer = st.radio(label=f"**Pergunta {idx + 1}:** {question['pergunta']}", options=options, index=None)
                            submitted = st.form_submit_button("Responder")

                            if submitted:
                                if user_answer is None:
                                    st.warning("Por favor, selecione uma resposta.")
                                else:
                                    st.session_state.answered = True
                                    if user_answer == question["resposta_correta"]:
                                        st.session_state.score += 1
                                        st.success(f"🎉 Correto! {question.get('justificativa', '')}")
                                    else:
                                        st.error(f"❌ Incorreto. Resposta certa: **{question['resposta_correta']}**. {question.get('justificativa', '')}")
                                        # Chamada para salvar erro, se a função existir
                                        # salvar_erro(...)
                        
                        if st.session_state.get("answered"):
                            if st.button("Próxima Pergunta ➡️"):
                                st.session_state.current_question += 1
                                st.session_state.answered = False
                                st.rerun()
                    else:
                        st.balloons()
                        st.success(f"🎉 Quiz Concluído! Pontuação final: {st.session_state.score}/{total_questions}")
                
                elif quiz_id_selecionado is not None:
                     st.warning("Este quiz não contém nenhuma questão. Pode ter ocorrido um erro durante a geração ou o tipo de dado da coluna 'quiz_id' pode estar incorreto.")

elif menu == "Revisar Erros":
    st.title("🧐 Revise Seus Erros")
    erros = listar_erros()
    if not erros:
        st.info("Nenhuma questão foi registrada como errada ainda.")
    else:
        for erro in erros:
            with st.container(border=True):
                st.markdown(f"**Pergunta:** {erro['pergunta']}")
                # Adicione mais detalhes se desejar
    
elif menu == "Flashcards":
    st.title("🗂️ Flashcards para Estudo")
    st.info("Funcionalidade em desenvolvimento.")
