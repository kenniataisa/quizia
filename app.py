import streamlit as st
import time
import json
from supabase import create_client, Client

# --- Configurações ---
st.set_page_config(page_title="Quizia Pro+", layout="wide")

# --- Inicialização do Supabase ---
SUPABASE_URL = st.secrets["SUPABASE_URL"]
SUPABASE_KEY = st.secrets["SUPABASE_KEY"]
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# --- Funções do Supabase ---
def salvar_questoes_no_supabase(nome_quiz, disciplina, questoes):
    try:
        for q in questoes:
            data = {
                "nome_quiz": nome_quiz,
                "disciplina": disciplina,
                "estilo": q.get("estilo"),
                "pergunta": q.get("pergunta") or q.get("texto_base") or q.get("pergunta_guia"),
                "opcoes": json.dumps(q.get("opcoes", []), ensure_ascii=False),
                "resposta_correta": q.get("resposta_correta") or ", ".join(q.get("respostas_aceitaveis", [])),
                "justificativa": q.get("justificativa", ""),
                "contexto_citado": q.get("contexto_citado", ""),
                "dificuldade": q.get("dificuldade", "Desconhecida")
            }
            supabase.table("quizzes").insert(data).execute()
        st.success(f"✅ Questões do quiz '{nome_quiz}' salvas com sucesso!")
    except Exception as e:
        st.error(f"Erro ao salvar questões: {e}")

def listar_disciplinas():
    try:
        data = supabase.table("quizzes").select("disciplina").execute().data
        if data:
            return sorted(list(set([d["disciplina"] for d in data if d["disciplina"]])))
        return []
    except Exception as e:
        st.error(f"Erro ao listar disciplinas: {e}")
        return []

def listar_questoes_por_disciplina(disciplina):
    try:
        return supabase.table("quizzes").select("*").eq("disciplina", disciplina).execute().data
    except Exception as e:
        st.error(f"Erro ao buscar questões: {e}")
        return []

# --- Tela Inicial ---
st.title("🤖 Quizia Pro+")
st.markdown("Plataforma de geração inteligente de quizzes com IA e Supabase.")

uploaded_file = st.file_uploader("📤 Envie o PDF do livro ou apostila", type=["pdf"])

if uploaded_file:
    st.info("Arquivo carregado com sucesso! Clique em 'Gerar Questões' para iniciar.")

    if st.button("🚀 Gerar Questões"):
        with st.spinner("Gerando questões com IA... Isso pode levar alguns segundos."):
            progress = st.progress(0)
            for i in range(100):
                time.sleep(0.03)
                progress.progress(i + 1)

        # Simulação de retorno de IA
        questoes_geradas = [
            {"pergunta": "O que é uma variável em Python?", 
             "opcoes": ["Um tipo de dado", "Um valor fixo", "Um espaço nomeado para armazenar dados"],
             "resposta_correta": "Um espaço nomeado para armazenar dados",
             "justificativa": "De acordo com o livro, uma variável é usada para armazenar valores temporariamente durante a execução do programa."}
        ]

        nome_quiz = st.text_input("Nome do Quiz:")
        disciplina = st.selectbox("Selecione a disciplina:", listar_disciplinas() + ["Nova disciplina"])
        if disciplina == "Nova disciplina":
            disciplina = st.text_input("Digite o nome da nova disciplina:")

        if st.button("💾 Salvar Quiz"):
            salvar_questoes_no_supabase(nome_quiz, disciplina, questoes_geradas)

# ==========================================================
# MENU LATERAL
# ==========================================================
st.sidebar.title("📚 Navegação")
menu = st.sidebar.radio(
    "Escolha uma opção:",
    ["Disciplinas", "Flashcards", "Revisão de Erros", "Configurar Estilos", "Configurar Dificuldade"]
)

# Criar um container limpo para cada aba
st.session_state.placeholder = st.empty()

# ----------------------------------------------------------
# 📘 1. MENU DISCIPLINAS
# ----------------------------------------------------------
if menu == "Disciplinas":
    with st.session_state.placeholder.container():
        disciplinas = listar_disciplinas()
        if not disciplinas:
            st.info("Nenhuma disciplina cadastrada ainda. Gere um quiz primeiro!")
        else:
            disciplina = st.selectbox("Selecione uma disciplina:", disciplinas)
            questoes = listar_questoes_por_disciplina(disciplina)

            if questoes:
                st.subheader(f"📖 Questões de {disciplina}")
                for i, q in enumerate(questoes):
                    pergunta = (
                        q.get("pergunta")
                        or q.get("texto_base")
                        or q.get("pergunta_guia")
                        or "Pergunta não disponível"
                    )

                    st.markdown(f"**{i+1}. {pergunta}**")

                    try:
                        opcoes = json.loads(q.get("opcoes", "[]"))
                    except Exception:
                        opcoes = []

                    if opcoes:
                        resposta = st.radio(
                            f"Escolha a resposta da questão {i+1}:",
                            opcoes,
                            key=f"resposta_{i}",
                        )

                        # Feedback imediato
                        correta = q.get("resposta_correta", "").strip()
                        if resposta:
                            if resposta.lower() == correta.lower():
                                st.success("✅ Correto!")
                            else:
                                st.error(f"❌ Incorreto! Resposta certa: **{correta}**")

                    st.caption(f"**Dificuldade:** {q.get('dificuldade', 'Desconhecida')}")
                    st.divider()

# ----------------------------------------------------------
# 🧩 2. MENU CONFIGURAR ESTILOS
# ----------------------------------------------------------
elif menu == "Configurar Estilos":
    with st.session_state.placeholder.container():
        st.header("🎨 Estilos de Questões")
        estilos = st.multiselect(
            "Selecione os estilos de questões que deseja permitir:",
            ["Múltipla Escolha", "Aberta", "Preencher Lacuna", "Associar Colunas", "Verdadeiro ou Falso"],
            default=["Múltipla Escolha", "Aberta"],
        )
        st.session_state.estilos_selecionados = estilos
        st.success("Estilos atualizados com sucesso!")

# ----------------------------------------------------------
# ⚙️ 3. MENU CONFIGURAR DIFICULDADE
# ----------------------------------------------------------
elif menu == "Configurar Dificuldade":
    with st.session_state.placeholder.container():
        st.header("📈 Níveis de Dificuldade")
        dificuldade = st.selectbox(
            "Escolha o nível de dificuldade:",
            ["Aleatório", "Fácil", "Médio", "Difícil"],
        )
        st.session_state.dificuldade = dificuldade
        st.success("Nível de dificuldade configurado!")

# ----------------------------------------------------------
# 🧠 4. MENU FLASHCARDS
# ----------------------------------------------------------
elif menu == "Flashcards":
    with st.session_state.placeholder.container():
        st.header("🧠 Flashcards")
        st.info("Funcionalidade em desenvolvimento. Em breve você poderá revisar conteúdo de forma interativa!")

# ----------------------------------------------------------
# ❌ 5. MENU REVISÃO DE ERROS
# ----------------------------------------------------------
elif menu == "Revisão de Erros":
    with st.session_state.placeholder.container():
        st.header("📋 Revisão de Erros")
        erros = supabase.table("erros").select("*").order("created_at", desc=True).execute().data
        if not erros:
            st.info("Nenhum erro registrado ainda.")
        else:
            for erro in erros:
                with st.container(border=True):
                    st.write(f"**Pergunta:** {erro.get('pergunta', '—')}")
                    st.write(f"**Sua Resposta:** {erro.get('resposta_usuario', '—')}")
                    st.write(f"**Correta:** {erro.get('resposta_correta', '—')}")
                    st.caption(erro.get("justificativa", ""))
