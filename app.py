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
st.set_page_config(page_title="Quizia Pro+", layout="wide", initial_sidebar_state="expanded")
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

# --- MODELOS DE IA (Geração e Avaliação) ---
MODELO_GERACAO = "tngtech/deepseek-r1t2-chimera:free"
MODELO_AVALIACAO = "google/gemma-3-27b-it:free"

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
            if extracted:
                text += extracted
        return text
    except Exception as e:
        st.error(f"Erro ao processar o PDF: {e}")
        return None

def chunk_text(text, chunk_size=8000, overlap=400):
    if not text:
        return []
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
    """
    Retorna a instrução de formato JSON correta para cada estilo de questão.
    """
    base_instruction = """"contexto_citado": "The exact quote or paragraph from the reference text used to create this question.",\n"""
    
    if estilo == "Aberta":
        return f"""
        Each question object must have these keys: "pergunta", "estilo", "resposta_ideal", and "contexto_citado".
        {base_instruction}
        "resposta_ideal" must be a detailed paragraph explaining the perfect answer, to be used as a rubric for AI-powered evaluation.
        """
    if estilo == "Preencher Lacuna":
        return f"""
        Each question object must have these keys: "estilo", "texto_base", "respostas_aceitaveis", and "contexto_citado".
        {base_instruction}
        "texto_base" must be a sentence or paragraph with the placeholder "[L_A_C_U_N_A]" where the blank should be.
        "respostas_aceitaveis" must be a list of one or more correct words/phrases for the blank.
        """
    if estilo == "Associar Colunas":
        return f"""
        Each question object must have these keys: "estilo", "pergunta_guia", "coluna_a", "coluna_b", "associacoes_corretas", and "contexto_citado".
        {base_instruction}
        "coluna_a" and "coluna_b" must be lists of strings.
        "associacoes_corretas" must be a dictionary mapping each item from "coluna_a" to its correct corresponding item in "coluna_b".
        """
    
    # --- NOVO: Bloco específico para Verdadeiro ou Falso ---
    if estilo == "Verdadeiro ou Falso":
        return f"""
        Each question object must have these keys: "pergunta", "estilo", "opcoes", "resposta_correta", "justificativa", and "contexto_citado".
        {base_instruction}
        "pergunta" MUST be a declarative statement that can be judged as true or false.
        "estilo" MUST be "Verdadeiro ou Falso".
        "opcoes" MUST be the list ["Verdadeiro", "Falso"].
        "resposta_correta" MUST be either "Verdadeiro" or "Falso".
        "justificativa" must explain why the statement is true or false, based on the context.
        """

    # Padrão: Múltipla Escolha (CORRIGIDO para evitar o bug a,b,c,d)
    return f"""
    Each question object must have these keys: "pergunta", "estilo", "opcoes", "resposta_correta", "justificativa", and "contexto_citado".
    {base_instruction}
    "pergunta" MUST contain only the question text. Do NOT include the 'a)', 'b)', 'c)', 'd)' prefixes in the question string.
    "estilo" MUST be "Múltipla Escolha".
    "opcoes" MUST be a list containing the FULL TEXT of each answer choice (e.g., ["Paris", "Londres", "Berlim"]).
    "resposta_correta" MUST be the full text of the correct option, exactly matching one of the items in the "opcoes" list (e.g., "Paris").

    Example of a PERFECT object:
    {{
        "pergunta": "Qual é a capital da França?",
        "estilo": "Múltipla Escolha",
        "opcoes": ["Londres", "Berlim", "Paris", "Roma"],
        "resposta_correta": "Paris",
        "justificativa": "Paris é a capital e a cidade mais populosa da França.",
        "contexto_citado": "A capital da França é Paris, ..."
    }}
    """

def generate_questions_for_chunk(text_chunk, estilos_list, dificuldade, num_questions_per_chunk):
    if not client:
        return None

    estilos_disponiveis = estilos_list if estilos_list else ["Múltipla Escolha"]
    estilo_escolhido = random.choice(estilos_disponiveis)
    
    niveis_disponiveis = ["Fácil", "Médio", "Difícil"]
    if dificuldade == "Aleatório":
        dificuldade = random.choice(niveis_disponiveis)
    
    json_format = get_json_format_instruction(estilo_escolhido)
    
    prompt_final = (
        f"You are an expert educator. Analyze the provided text and create {num_questions_per_chunk} insightful, contextualized questions that require understanding and synthesis of concepts, not just rote memorization. "
        f"Vary the approach to the topics; do not ask the same core concept in multiple questions. "
        f"The questions must be in the '{estilo_escolhido}' style with a '{dificuldade}' difficulty level. "
        f"You must respond strictly in a JSON format containing an object with the key 'questoes', which holds a list of question objects. "
        f"{json_format}"
        f"The entire JSON response, including all keys and values, must be in Brazilian Portuguese (pt-BR).\n\n"
        f"Reference Text:\n{text_chunk}"
    )

    try:
        completion = client.chat.completions.create(
            model=MODELO_GERACAO, # Usa o modelo de geração
            messages=[{"role": "user", "content": prompt_final}],
            response_format={"type": "json_object"},
            timeout=240
        )
        response_content = completion.choices[0].message.content
        if response_content:
            questoes = json.loads(response_content).get("questoes")
            return questoes
    except Exception as e:
        print(f"--- ERRO ao chamar o modelo {MODELO_GERACAO}: {e} ---")
        return None

# ======================
# FUNÇÃO DE AVALIAÇÃO (IA)
# ======================
def evaluate_open_answer_with_ai(question, ideal_answer, user_answer):
    if not client:
        return {"nota": 0, "feedback": "Cliente de IA não configurado."}
    
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
            model=MODELO_AVALIACAO, # NOVO: Usa o modelo de avaliação
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            timeout=120
        )
        response_content = completion.choices[0].message.content
        if response_content:
            evaluation = json.loads(response_content)
            if "nota" in evaluation and "feedback" in evaluation:
                return evaluation
    except Exception as e:
        print(f"--- ERRO ao chamar o modelo {MODELO_AVALIACAO}: {e} ---")
    
    return {"nota": 0, "feedback": f"Ocorreu um erro ao tentar avaliar sua resposta com o modelo {MODELO_AVALIACAO}."}

# ======================
# FUNÇÕES DE BANCO DE DADOS
# ======================
def salvar_erro(question_data, user_answer):
    if not supabase:
        return
    try:
        error_log = {
            "pergunta": question_data.get("pergunta") or question_data.get("texto_base") or question_data.get("pergunta_guia"),
            "resposta_correta": question_data.get("resposta_correta") or ", ".join(question_data.get("respostas_aceitaveis", [])) or json.dumps(question_data.get("associacoes_corretas"), ensure_ascii=False),
            "resposta_usuario": user_answer,
            "estilo": question_data.get("estilo"),
            "opcoes": json.dumps(question_data.get("opcoes", []), ensure_ascii=False),
            "justificativa": question_data.get("justificativa") or f"Nota IA: {st.session_state.last_evaluation.get('nota') if st.session_state.last_evaluation else 'N/A'}",
            "contexto_citado": question_data.get("contexto_citado", "Contexto não fornecido pela IA.")
        }
        supabase.table("erros").insert(error_log).execute()
        st.toast("Ops! Erro registado para sua revisão.", icon="💔")
    except Exception as e:
        st.error(f"Erro ao salvar erro no Supabase: {e}")

def listar_erros():
    if not supabase:
        return []
    try:
        return supabase.table("erros").select("*").order("created_at", desc=True).execute().data
    except Exception as e:
        st.error(f"Erro ao listar erros do Supabase: {e}")
        return []

# ======================
# INICIALIZAÇÃO DO ESTADO
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
st.sidebar.title("Quizia Pro+")
menu = st.sidebar.radio("Menu", ["Gerar e Resolver Quiz", "Revisar Erros", "Flashcards"])

# ------------------ MENU GERAR QUIZ ------------------
if menu == "Gerar e Resolver Quiz":
    if not st.session_state.quiz_started:
        st.title("➕ Gerar Novo Quiz a partir de um PDF")
        st.markdown("O conteúdo do seu PDF será transformado em um quiz com diferentes tipos de questões.")
        
        with st.container(border=True):
            uploaded_file = st.file_uploader("1. Selecione o arquivo PDF", type=["pdf"])
            col1, col2 = st.columns(2)
            with col1:
                dificuldade = st.selectbox("2. Dificuldade", ["Aleatório", "Fácil", "Médio", "Difícil"])
            with col2:
                # --- MUDANÇA: Adicionado "Verdadeiro ou Falso" ---
                estilos_selecionados = st.multiselect(
                    "3. Estilos de Questão", 
                    options=["Múltipla Escolha", "Aberta", "Preencher Lacuna", "Associar Colunas", "Verdadeiro ou Falso"], 
                    default=["Múltipla Escolha", "Aberta", "Verdadeiro ou Falso"]
                )
            
            num_questoes = st.number_input("4. Número Total de Questões", min_value=1, max_value=50, value=10)
            
            if st.button("Analisar e Gerar Quiz", type="primary", disabled=(not client or not uploaded_file)):
                with st.status("Gerando seu quiz...", expanded=True) as status:
                    status.update(label="Extraindo texto...", state="running")
                    pdf_text = extract_text_from_pdf(uploaded_file)
                    if not pdf_text:
                        status.update(label="Falha ao extrair texto.", state="error")
                        st.stop()
                    
                    status.update(label="Dividindo conteúdo...", state="running")
                    chunks = chunk_text(pdf_text)
                    if not chunks:
                         status.update(label="PDF sem texto legível.", state="error")
                         st.stop()
                    
                    q_per_chunk = max(1, int(num_questoes / len(chunks)) + 1)
                    
                    status.update(label=f"Gerando questões com a IA (Modelo: {MODELO_GERACAO})...", state="running")
                    all_generated_questions = []
                    with concurrent.futures.ThreadPoolExecutor() as executor:
                        future_to_chunk = {
                            executor.submit(generate_questions_for_chunk, chunk, estilos_selecionados, dificuldade, q_per_chunk): chunk 
                            for chunk in chunks
                        }
                        for future in concurrent.futures.as_completed(future_to_chunk):
                            questions_data = future.result()
                            if questions_data:
                                all_generated_questions.extend(questions_data)
                    
                    if not all_generated_questions:
                        status.update(label="A IA não conseguiu gerar questões para este documento.", state="error")
                        st.stop()

                    random.shuffle(all_generated_questions)
                    st.session_state.quiz_data = all_generated_questions[:num_questoes]
                    st.session_state.quiz_started = True
                    st.rerun()

    # Quando o quiz já foi iniciado
    if st.session_state.quiz_started:
        st.title("🧠 Quiz em Andamento")
        total_questions = len(st.session_state.quiz_data)
        idx = st.session_state.current_question
        max_score = total_questions * 10
        st.subheader(f"Pontuação: {st.session_state.score:.1f} / {max_score}")

        if idx < total_questions:
            question = st.session_state.quiz_data[idx]
            estilo_q = question.get("estilo", "Múltipla Escolha")

            # --- MUDANÇA: "Múltipla Escolha" e "Verdadeiro ou Falso" usam a mesma lógica ---
            if estilo_q in ['Múltipla Escolha', 'Verdadeiro ou Falso']:
                st.markdown(f"**Pergunta {idx + 1}:** {question['pergunta']}")
                with st.form(key=f"form_radio_{idx}"): # Chave unificada
                    user_answer = st.radio("Opções:", options=question.get("opcoes", []), index=None)
                    submitted = st.form_submit_button("Responder")
                    if submitted and user_answer is not None:
                        st.session_state.answered = True
                        if str(user_answer).strip() == str(question["resposta_correta"]).strip():
                            st.success(f"🎉 Correto! {question.get('justificativa', '')}")
                            st.session_state.score += 10
                        else:
                            st.error(f"❌ Incorreto. Resposta certa: **{question['resposta_correta']}**. {question.get('justificativa', '')}")
                            salvar_erro(question, user_answer)
            
            # ------- Questão: Aberta (com Avaliação IA) -------
            elif estilo_q == 'Aberta':
                st.markdown(f"**Pergunta {idx + 1}:** {question['pergunta']}")
                with st.form(key=f"form_aberta_{idx}"):
                    user_answer = st.text_area("Sua Resposta:", height=150)
                    submitted = st.form_submit_button("Avaliar Resposta com IA")
                    if submitted and user_answer:
                        with st.spinner(f"Avaliando sua resposta com o {MODELO_AVALIACAO}..."):
                            evaluation = evaluate_open_answer_with_ai(
                                question['pergunta'],
                                question['resposta_ideal'],
                                user_answer
                            )
                        st.session_state.answered = True
                        st.session_state.last_evaluation = evaluation
                        nota = evaluation.get("nota", 0)
                        st.session_state.score += nota
                        if nota < 7:
                            salvar_erro(question, user_answer)
                        
                        if nota >= 7: st.success(f"Ótima resposta! Nota: {nota}/10")
                        elif nota >= 5: st.warning(f"Resposta razoável. Nota: {nota}/10")
                        else: st.error(f"Resposta precisa de melhorias. Nota: {nota}/10")
                        
                        st.info(f"**Feedback da IA:** {evaluation.get('feedback', '')}")
                        with st.expander("Ver gabarito completo (Resposta Ideal)"):
                            st.info(f"{question['resposta_ideal']}")

            # ------- Questão: Preencher Lacuna -------
            elif estilo_q == 'Preencher Lacuna':
                st.markdown(f"**Pergunta {idx + 1}:** {question['texto_base'].replace('[L_A_C_U_N_A]', '___________')}")
                with st.form(key=f"form_lacuna_{idx}"):
                    user_answer = st.text_input("Sua Resposta:")
                    submitted = st.form_submit_button("Responder")
                    if submitted and user_answer:
                        st.session_state.answered = True
                        respostas_aceitaveis = [str(r).strip().lower() for r in question.get("respostas_aceitaveis", [])]
                        if str(user_answer).strip().lower() in respostas_aceitaveis:
                            st.success("🎉 Correto!")
                            st.session_state.score += 10
                        else:
                            st.error(f"❌ Incorreto. Respostas aceitáveis: **{', '.join(question['respostas_aceitaveis'])}**")
                            salvar_erro(question, user_answer)

            # ------- Questão: Associar Colunas -------
            elif estilo_q == 'Associar Colunas':
                st.markdown(f"**Pergunta {idx + 1}:** {question['pergunta_guia']}")
                col_a = question.get('coluna_a', [])
                col_b = question.get('coluna_b', [])
                random.shuffle(col_b) # Embaralha as opções da coluna B
                
                with st.form(key=f"form_assoc_{idx}"):
                    user_associations = {}
                    st.markdown("---")
                    for item_a in col_a:
                        user_associations[item_a] = st.selectbox(f"**{item_a}** associa-se com:", options=[""] + col_b, index=0)
                    st.markdown("---")
                    submitted = st.form_submit_button("Responder")
                    
                    if submitted:
                        st.session_state.answered = True
                        correct_answers = question.get("associacoes_corretas", {})
                        score_per_item = 10 / max(1, len(correct_answers))
                        current_score = 0
                        all_correct = True
                        
                        for item_a, user_b in user_associations.items():
                            if str(correct_answers.get(item_a)).strip() == str(user_b).strip():
                                current_score += score_per_item
                            else:
                                all_correct = False
                        
                        st.session_state.score += current_score
                        if all_correct:
                            st.success(f"🎉 Correto! Pontuação: {current_score:.1f}/10")
                        else:
                            st.error(f"Respostas parcialmente corretas. Pontuação: {current_score:.1f}/10")
                            salvar_erro(question, json.dumps(user_associations, ensure_ascii=False))
                        
                        with st.expander("Ver Gabarito Completo"):
                            st.json(correct_answers)

            # Botão de Próxima Pergunta
            if st.session_state.get("answered"):
                if st.button("Próxima Pergunta ➡️"):
                    st.session_state.current_question += 1
                    st.session_state.answered = False
                    st.session_state.last_evaluation = None
                    st.rerun()
        else:
            # Tela de Fim de Quiz
            st.balloons()
            st.success(f"🎉 Quiz Concluído! Pontuação final: {st.session_state.score:.1f} / {max_score}")
            if st.button("Gerar Novo Quiz"):
                initialize_session()
                st.rerun()

# ------------------ MENU REVISAR ERROS ------------------
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
                
                if erro.get('justificativa'):
                    st.info(f"**Justificativa:** {erro['justificativa']}")
                
                if erro.get('contexto_citado'):
                    with st.expander("Ver contexto do PDF (Onde a resposta estava)"):
                        st.info(f"{erro['contexto_citado']}")

# ------------------ MENU FLASHCARDS ------------------
elif menu == "Flashcards":
    st.title("🗂️ Flashcards para Estudo")
    st.info("Funcionalidade em desenvolvimento.")
