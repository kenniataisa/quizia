
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
    # Padrão: Múltipla Escolha
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
        f"If possible, use your external knowledge to create relevant analogies or examples. "
        f"The questions must be in the '{estilo}' style with a '{dificuldade}' difficulty level. "
        f"You must respond strictly in a JSON format containing an object with the key 'questoes', which holds a list of question objects. "
        f"{json_format}"
        f"The entire JSON response, including all keys and values, must be in Brazilian Portuguese (pt-BR).\n\n"
        f"Reference Text:\n{text_chunk}"
    )
    try:
        completion = client.chat.completions.create(
            model="openai/gpt-4o", # Recomendo um modelo mais forte para esses formatos complexos
            messages=[{"role": "user", "content": prompt_final}],
            response_format={"type": "json_object"},
            timeout=240
        )
        response_content = completion.choices[0].message.content
        if not response_content: return None
        return json.loads(response_content).get("questoes")
    except Exception as e:
        print(f"Erro ao chamar a API de geração: {e}"); return None

# ======================
# FUNÇÕES DE AVALIAÇÃO (IA)
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
            model="openai/gpt-4o",
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
# FUNÇÕES DE BANCO DE DADOS (APENAS PARA ERROS) E CORE
# ======================
def salvar_erro(data):
    if not supabase: return
    try:
        supabase.table("erros").insert(data).execute()
        st.toast("Ops! Erro registado para sua revisão.", icon="💔")
    except Exception as e:
        st.error(f"Erro ao salvar erro no Supabase: {e}")

def listar_erros():
    # ... (sua função de listar erros)
    pass

def extract_text_from_pdf(uploaded_file):
    # ... (sua função de extrair texto)
    pass
    
# ... (demais funções utilitárias)

# ======================
# INTERFACE STREAMLIT
# ======================
# ... (código de inicialização e menu) ...

if menu == "Gerar e Resolver Quiz":
    # ... (código para gerar o quiz, que salva em st.session_state.quiz_data)

    if st.session_state.quiz_started:
        st.title("🧠 Quiz em Andamento")
        idx = st.session_state.current_question
        question = st.session_state.quiz_data[idx]
        
        # --- LÓGICA DE RENDERIZAÇÃO DINÂMICA ---
        
        if question['estilo'] == 'Múltipla Escolha':
            # ... (seu código de formulário com st.radio)
            pass

        elif question['estilo'] == 'Aberta':
            st.markdown(f"**Pergunta {idx + 1}:** {question['pergunta']}")
            with st.form(key=f"form_aberta_{idx}"):
                user_answer = st.text_area("Sua Resposta:", height=200)
                submitted = st.form_submit_button("Avaliar Resposta")

                if submitted and user_answer:
                    with st.spinner("Avaliando sua resposta com a IA..."):
                        evaluation = evaluate_open_answer_with_ai(question['pergunta'], question['resposta_ideal'], user_answer)
                    
                    st.session_state.answered = True
                    st.session_state.last_evaluation = evaluation
                    
                    nota = evaluation.get("nota", 0)
                    st.session_state.score += nota # Adiciona a nota à pontuação
                    
                    if nota >= 7:
                        st.success(f"Ótima resposta! Nota: {nota}/10")
                    elif nota >= 5:
                        st.warning(f"Resposta razoável. Nota: {nota}/10")
                    else:
                        st.error(f"Resposta precisa de melhorias. Nota: {nota}/10")
                    
                    st.info(f"**Feedback:** {evaluation.get('feedback', '')}")
                    st.info(f"**Resposta Ideal (Gabarito):** {question['resposta_ideal']}")

        elif question['estilo'] == 'Preencher Lacuna':
            st.markdown(f"**Pergunta {idx + 1}:** Complete a frase abaixo:")
            st.markdown(f"> {question['texto_base'].replace('[L_A_C_U_N_A]', '___________')}")
            
            with st.form(key=f"form_lacuna_{idx}"):
                user_answer = st.text_input("Sua resposta para a lacuna:")
                submitted = st.form_submit_button("Verificar")

                if submitted and user_answer:
                    st.session_state.answered = True
                    respostas_corretas = [r.lower().strip() for r in question['respostas_aceitaveis']]
                    if user_answer.lower().strip() in respostas_corretas:
                        st.success("🎉 Correto!")
                        st.session_state.score += 10 # Pontuação máxima
                    else:
                        st.error(f"❌ Incorreto. Respostas aceitáveis eram: **{', '.join(question['respostas_aceitaveis'])}**")

        elif question['estilo'] == 'Associar Colunas':
            st.markdown(f"**Pergunta {idx + 1}:** {question['pergunta_guia']}")
            
            with st.form(key=f"form_assoc_{idx}"):
                col1, col2 = st.columns(2)
                user_associations = {}
                shuffled_col_b = random.sample(question['coluna_b'], len(question['coluna_b']))
                
                with col1:
                    st.subheader("Coluna A")
                    for item_a in question['coluna_a']:
                        st.markdown(f"- **{item_a}**")

                with col2:
                    st.subheader("Coluna B")
                    for i, item_a in enumerate(question['coluna_a']):
                        user_associations[item_a] = st.selectbox(f"Corresponde a '{item_a}':", options=shuffled_col_b, key=f"select_{idx}_{i}", index=None)

                submitted = st.form_submit_button("Verificar Associações")
                
                if submitted:
                    st.session_state.answered = True
                    acertos = 0
                    total_itens = len(question['coluna_a'])
                    
                    for item_a, item_b_usuario in user_associations.items():
                        if question['associacoes_corretas'][item_a] == item_b_usuario:
                            acertos += 1
                    
                    st.session_state.score += (acertos / total_itens) * 10 # Pontuação proporcional

                    st.info(f"Você acertou {acertos} de {total_itens} associações.")
                    if acertos == total_itens:
                        st.success("🎉 Perfeito! Todas as associações estão corretas.")
                    else:
                        st.error("Algumas associações estão incorretas. Veja o gabarito abaixo:")
                        with st.expander("Mostrar Gabarito"):
                            for item_a, item_b_correto in question['associacoes_corretas'].items():
                                st.markdown(f"- **{item_a}** ➡️ **{item_b_correto}**")

        # --- Lógica de Próxima Pergunta ---
        if st.session_state.get("answered"):
            if st.button("Próxima Pergunta ➡️"):
                st.session_state.current_question += 1
                st.session_state.answered = False
                st.session_state.last_evaluation = None
                st.rerun()

# ... (Restante do código, como a página "Revisar Erros")

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
