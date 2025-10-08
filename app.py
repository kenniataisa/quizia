import streamlit as st
import io
from pypdf import PdfReader
import openai
from supabase import create_client, Client
import random
import os
from dotenv import load_dotenv
import json
import concurrent.futures

# ======================
# CONFIGURAÇÃO INICIAL
# ======================
st.set_page_config(page_title="Quizia Pro", layout="wide", initial_sidebar_state="expanded")
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
# FUNÇÕES CORE (PDF, CHUNKS)
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

# ======================
# FUNÇÕES DE GERAÇÃO (IA)
# ======================
def get_json_format_instruction(estilo):
    """Retorna a instrução de formato JSON correta para cada estilo de questão."""
    if estilo == "Aberta":
        return """
        Each question object must have these keys: "pergunta", "estilo", and "resposta_ideal".
        "resposta_ideal" must be a detailed paragraph explaining the perfect answer, to be used as a rubric for AI-powered evaluation.
        """
    if estilo == "Preencher Lacuna":
        return """
        Each question object must have these keys: "estilo", "texto_base", and "respostas_aceitaveis".
        "texto_base" must be a sentence or paragraph with the placeholder "[L_A_C_U_N_A]" where the blank should be.
        "respostas_aceitaveis" must be a list of one or more correct words/phrases for the blank.
        """
    if estilo == "Associar Colunas":
        return """
        Each question object must have these keys: "estilo", "pergunta_guia", "coluna_a", "coluna_b", and "associacoes_corretas".
        "coluna_a" and "coluna_b" must be lists of strings.
        "associacoes_corretas" must be a dictionary mapping each item from "coluna_a" to its correct corresponding item in "coluna_b".
        """
    return """
    Each question object must have these keys: "pergunta", "estilo", "opcoes", "resposta_correta", and "justificativa".
    """

def generate_questions_for_chunk(text_chunk, estilo, dificuldade):
    if not client: return None
    
    estilos_disponiveis = ["Múltipla Escolha", "Aberta", "Preencher Lacuna", "Associar Colunas"]
    if estilo == "Aleatório": estilo = random.choice(estilos_disponiveis)
    
    niveis_disponiveis = ["Fácil", "Médio", "Difícil"]
    if dificuldade == "Aleatório": dificuldade = random.choice(niveis_disponiveis)
    
    json_format = get_json_format_instruction(estilo)
    
    prompt_final = (
        f"You are an expert educator. Analyze the provided text and create insightful, contextualized questions that require understanding and synthesis of concepts, not just rote memorization. "
        f"The questions must be in the '{estilo}' style with a '{dificuldade}' difficulty level. "
        f"You must respond strictly in a JSON format containing an object with the key 'questoes', which holds a list of question objects. "
        f"{json_format}"
        f"The entire JSON response, including all keys and values, must be in Brazilian Portuguese (pt-BR).\n\n"
        f"Reference Text:\n{text_chunk}"
    )
    try:
        completion = client.chat.completions.create(
            # MODELO ALTERADO DE VOLTA PARA O DEEPSEEK
            model="deepseek/deepseek-chat-v3.1:free",
            messages=[{"role": "user", "content": prompt_final}],
            response_format={"type": "json_object"},
            timeout=240
        )
        response_content = completion.choices[0].message.content
        print("--- RESPOSTA CRUA DA IA ---")
        print(response_content)
        print("---------------------------")
        # ------------------------------------
        if not response_content: return None
        return json.loads(response_content).get("questoes")
    except Exception as e:
        print(f"Erro ao chamar a API de geração: {e}"); return None

# ======================
# FUNÇÃO DE AVALIAÇÃO (IA)
# ======================
def evaluate_open_answer_with_ai(question, ideal_answer, user_answer):
    if not client: return {"nota": 0, "feedback": "Cliente de IA não configurado."}
    
    prompt = f"""
    As an AI teaching assistant, evaluate the user's answer based on the provided question and the ideal answer key (rubric).
    Provide a score from 0 to 10 and constructive feedback. The score should reflect how well the user's answer aligns with the key concepts of the ideal answer.

    Question: "{question}"
    Ideal Answer Key: "{ideal_answer}"
    User's Answer: "{user_answer}"

    You must respond strictly in the following JSON format: {{"nota": <score_number>, "feedback": "<feedback_text_in_portuguese>"}}
    The feedback text MUST be in Brazilian Portuguese (pt-BR).
    """
    try:
        completion = client.chat.completions.create(
            # MODELO ALTERADO DE VOLTA PARA O DEEPSEEK
            model="deepseek/deepseek-chat-v3.1:free",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            timeout=120
        )
        response_content = completion.choices[0].message.content
        return json.loads(response_content)
    except Exception as e:
        print(f"Erro ao chamar a API de avaliação: {e}")
        return {"nota": 0, "feedback": "Ocorreu um erro ao tentar avaliar sua resposta."}

# ======================
# FUNÇÕES DE BANCO DE DADOS (APENAS PARA ERROS)
# ======================
def salvar_erro(question_data, user_answer):
    if not supabase: return
    try:
        # Prepara um dicionário com os dados do erro para salvar
        error_log = {
            "pergunta": question_data.get("pergunta") or question_data.get("texto_base"),
            "resposta_correta": question_data.get("resposta_correta") or ", ".join(question_data.get("respostas_aceitaveis", [])),
            "resposta_usuario": user_answer,
            "estilo": question_data.get("estilo"),
            "opcoes": json.dumps(question_data.get("opcoes", []), ensure_ascii=False),
            "justificativa": question_data.get("justificativa")
        }
        supabase.table("erros").insert(error_log).execute()
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
# INICIALIZAÇÃO DO ESTADO DA SESSÃO
# ======================
def initialize_session():
    st.session_state.quiz_started = False
    st.session_state.quiz_data = []
    st.session_state.current_question = 0
    st.session_state.score = 0
    st.session_state.answered = False
    st.session_state.last_evaluation = None

if 'quiz_started' not in st.session_state:
    initialize_session()

# ======================
# INTERFACE STREAMLIT
# ======================
st.sidebar.title("Navegação")
menu = st.sidebar.radio("Menu", ["Gerar e Resolver Quiz", "Revisar Erros", "Flashcards"])

if menu == "Gerar e Resolver Quiz":
    if not st.session_state.quiz_started:
        st.title("➕ Gerar Novo Quiz a partir de um PDF")
        st.markdown("O conteúdo do seu PDF será transformado em um quiz com diferentes tipos de questões.")
        
        with st.container(border=True):
            uploaded_file = st.file_uploader("1. Selecione o arquivo PDF", type=["pdf"])
            col1, col2 = st.columns(2)
            with col1: dificuldade = st.selectbox("2. Dificuldade", ["Fácil", "Médio", "Difícil", "Aleatório"])
            with col2: estilo = st.selectbox("3. Estilo", ["Aleatório", "Múltipla Escolha", "Aberta", "Preencher Lacuna", "Associar Colunas"])
            
            if st.button("Analisar e Gerar Quiz", type="primary", disabled=(not client or not uploaded_file)):
                with st.status("Gerando seu quiz...", expanded=True) as status:
                    status.update(label="Extraindo texto...", state="running")
                    pdf_text = extract_text_from_pdf(uploaded_file)
                    if not pdf_text: status.update(label="Falha ao extrair texto.", state="error"); st.stop()
                    
                    status.update(label="Dividindo conteúdo...", state="running")
                    chunks = chunk_text(pdf_text)
                    
                    status.update(label="Gerando questões com a IA...", state="running")
                    all_generated_questions = []
                    with concurrent.futures.ThreadPoolExecutor() as executor:
                        future_to_chunk = {executor.submit(generate_questions_for_chunk, chunk, estilo, dificuldade): chunk for chunk in chunks}
                        for future in concurrent.futures.as_completed(future_to_chunk):
                            questions_data = future.result()
                            if questions_data: all_generated_questions.extend(questions_data)
                    
                    if not all_generated_questions: status.update(label="A IA não gerou questões.", state="error"); st.stop()

                    st.session_state.quiz_data = all_generated_questions
                    st.session_state.quiz_started = True
                    st.rerun()

    if st.session_state.quiz_started:
        st.title("🧠 Quiz em Andamento")
        total_questions = len(st.session_state.quiz_data)
        idx = st.session_state.current_question
        max_score = total_questions * 10
        st.subheader(f"Pontuação: {st.session_state.score:.1f} / {max_score}")

        if idx < total_questions:
            question = st.session_state.quiz_data[idx]
            estilo_q = question.get("estilo", "Múltipla Escolha")

            if estilo_q == 'Múltipla Escolha':
                st.markdown(f"**Pergunta {idx + 1}:** {question['pergunta']}")
                with st.form(key=f"form_multi_{idx}"):
                    user_answer = st.radio("Opções:", options=question.get("opcoes", []), index=None)
                    submitted = st.form_submit_button("Responder")
                    if submitted and user_answer is not None:
                        st.session_state.answered = True
                        if user_answer == question["resposta_correta"]:
                            st.success(f"🎉 Correto! {question.get('justificativa', '')}")
                            st.session_state.score += 10
                        else:
                            st.error(f"❌ Incorreto. Resposta certa: **{question['resposta_correta']}**. {question.get('justificativa', '')}")
                            salvar_erro(question, user_answer) # SALVANDO O ERRO
            
            elif estilo_q == 'Aberta':
                st.markdown(f"**Pergunta {idx + 1}:** {question['pergunta']}")
                with st.form(key=f"form_aberta_{idx}"):
                    user_answer = st.text_area("Sua Resposta:", height=150)
                    submitted = st.form_submit_button("Avaliar Resposta com IA")
                    if submitted and user_answer:
                        with st.spinner("Avaliando sua resposta..."):
                            evaluation = evaluate_open_answer_with_ai(question['pergunta'], question['resposta_ideal'], user_answer)
                        st.session_state.answered = True
                        st.session_state.last_evaluation = evaluation
                        nota = evaluation.get("nota", 0)
                        st.session_state.score += nota
                        if nota < 7: salvar_erro({"pergunta": question.get("pergunta"), "resposta_correta": question.get("resposta_ideal")}, user_answer)
                        if nota >= 7: st.success(f"Ótima resposta! Nota: {nota}/10")
                        elif nota >= 5: st.warning(f"Resposta razoável. Nota: {nota}/10")
                        else: st.error(f"Resposta precisa de melhorias. Nota: {nota}/10")
                        st.info(f"**Feedback da IA:** {evaluation.get('feedback', '')}")
                        with st.expander("Ver gabarito completo"): st.info(f"{question['resposta_ideal']}")

            elif estilo_q == 'Preencher Lacuna':
                st.markdown(f"**Pergunta {idx + 1}:** Complete a frase:")
                st.markdown(f"> `{question['texto_base'].replace('[L_A_C_U_N_A]', '___________')}`")
                with st.form(key=f"form_lacuna_{idx}"):
                    user_answer = st.text_input("Sua resposta para a lacuna:")
                    submitted = st.form_submit_button("Verificar")
                    if submitted and user_answer:
                        st.session_state.answered = True
                        respostas_corretas = [r.lower().strip() for r in question['respostas_aceitaveis']]
                        if user_answer.lower().strip() in respostas_corretas:
                            st.success("🎉 Correto!")
                            st.session_state.score += 10
                        else:
                            st.error(f"❌ Incorreto. Respostas aceitáveis: **{', '.join(question['respostas_aceitaveis'])}**")
                            salvar_erro(question, user_answer)

            elif estilo_q == 'Associar Colunas':
                st.markdown(f"**Pergunta {idx + 1}:** {question['pergunta_guia']}")
                with st.form(key=f"form_assoc_{idx}"):
                    col1, col2 = st.columns(2)
                    user_associations = {}
                    shuffled_col_b = random.sample(question['coluna_b'], len(question['coluna_b']))
                    with col1:
                        st.subheader("Coluna A")
                        for item_a in question['coluna_a']: st.markdown(f"- **{item_a}**")
                    with col2:
                        st.subheader("Coluna B")
                        for i, item_a in enumerate(question['coluna_a']):
                            user_associations[item_a] = st.selectbox(f"'{item_a}' corresponde a:", options=shuffled_col_b, key=f"select_{idx}_{i}", index=None)
                    submitted = st.form_submit_button("Verificar Associações")
                    if submitted:
                        st.session_state.answered = True
                        acertos = sum(1 for item_a, item_b in user_associations.items() if question['associacoes_corretas'].get(item_a) == item_b and item_b is not None)
                        total_itens = len(question['coluna_a'])
                        st.session_state.score += (acertos / total_itens) * 10
                        st.info(f"Você acertou {acertos} de {total_itens} associações.")
                        if acertos < total_itens:
                            salvar_erro({"pergunta": question.get("pergunta_guia"), "resposta_correta": json.dumps(question.get("associacoes_corretas"), ensure_ascii=False)}, json.dumps(user_associations, ensure_ascii=False))
                            with st.expander("Mostrar Gabarito"):
                                for item_a, item_b_correto in question['associacoes_corretas'].items(): st.markdown(f"- **{item_a}** ➡️ **{item_b_correto}**")

            if st.session_state.get("answered"):
                if st.button("Próxima Pergunta ➡️"):
                    st.session_state.current_question += 1
                    st.session_state.answered = False
                    st.session_state.last_evaluation = None
                    st.rerun()
        else:
            st.balloons()
            st.success(f"🎉 Quiz Concluído! Pontuação final: {st.session_state.score:.1f} / {max_score}")
            if st.button("Gerar Novo Quiz"):
                initialize_session()
                st.rerun()

elif menu == "Revisar Erros":
    st.title("🧐 Revise Seus Erros")
    st.markdown("Aqui estão as questões que você errou para que possa revisar e aprender.")
    erros = listar_erros()
    if not erros:
        st.info("Você ainda não errou nenhuma questão. Parabéns!")
    else:
        for erro in erros:
            with st.container(border=True):
                st.markdown(f"**Pergunta:** {erro['pergunta']}")
                st.markdown(f"**Sua resposta:** <span style='color:red;'>{erro['resposta_usuario']}</span>", unsafe_allow_html=True)
                st.markdown(f"**Resposta correta:** <span style='color:green;'>{erro['resposta_correta']}</span>", unsafe_allow_html=True)
                if erro.get('justificativa'): st.info(f"**Justificativa:** {erro['justificativa']}")

elif menu == "Flashcards":
    st.title("🗂️ Flashcards para Estudo")
    st.info("Funcionalidade em desenvolvimento.")
