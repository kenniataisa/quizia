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

# --- MODELO ÚNICO DE IA ---
MODELO_UNICO = "tngtech/deepseek-r1t2-chimera:free"

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
    if not client:
        return None

    estilos_disponiveis = ["Múltipla Escolha", "Aberta", "Preencher Lacuna", "Associar Colunas"]
    if estilo == "Aleatório":
        estilo = random.choice(estilos_disponiveis)
    
    niveis_disponiveis = ["Fácil", "Médio", "Difícil"]
    if dificuldade == "Aleatório":
        dificuldade = random.choice(niveis_disponiveis)
    
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
            model=MODELO_UNICO,
            messages=[{"role": "user", "content": prompt_final}],
            response_format={"type": "json_object"},
            timeout=240
        )
        response_content = completion.choices[0].message.content
        if response_content:
            questoes = json.loads(response_content).get("questoes")
            return questoes
    except Exception as e:
        print(f"--- ERRO ao chamar o modelo {MODELO_UNICO}: {e} ---")
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
            model=MODELO_UNICO,
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
        print(f"--- ERRO ao chamar o modelo {MODELO_UNICO}: {e} ---")
    
    return {"nota": 0, "feedback": "Ocorreu um erro ao tentar avaliar sua resposta com o modelo DeepSeek."}

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
            "justificativa": question_data.get("justificativa") or f"Nota IA: {st.session_state.last_evaluation.get('nota') if st.session_state.last_evaluation else 'N/A'}"
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
