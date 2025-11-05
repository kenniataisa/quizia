import streamlit as st
import fitz  # PyMuPDF
import json
from supabase import create_client, Client
from openai import OpenAI
import time
import uuid

# -------------------------------
# 🔑 Configurações
# -------------------------------
# Certifique-se de que suas chaves estão no Streamlit Secrets
SUPABASE_URL = st.secrets.get("SUPABASE_URL", "SUA_URL_AQUI")
SUPABASE_KEY = st.secrets.get("SUPABASE_KEY", "SUA_CHAVE_AQUI")
OPENROUTER_API_KEY = st.secrets.get("OPENROUTER_API_KEY", "SUA_CHAVE_AQUI")

# Verificação de inicialização
if SUPABASE_URL == "SUA_URL_AQUI" or SUPABASE_KEY == "SUA_CHAVE_AQUI" or OPENROUTER_API_KEY == "SUA_CHAVE_AQUI":
    st.error("As chaves de API (Supabase, OpenRouter) não foram configuradas nos 'Secrets' do Streamlit.")
    st.stop()

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# -------------------------------
# 🔧 Inicializa clientes OpenRouter
# -------------------------------
def create_openrouter_client():
    return OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=OPENROUTER_API_KEY,
    )

deepseek_client = create_openrouter_client()
llama_client = create_openrouter_client()

# -------------------------------
# 📘 Função: Extrair texto do PDF
# -------------------------------
def extract_text_from_pdf(uploaded_file):
    try:
        doc = fitz.open(stream=uploaded_file.read(), filetype="pdf")
        text = ""
        for page in doc:
            text += page.get_text("text") + "\n"
        return text.strip()
    except Exception as e:
        st.error(f"Erro ao ler o PDF: {e}")
        return ""

# -------------------------------
# ✂️ Dividir texto em chunks
# -------------------------------
def chunk_text(text, max_chars=3000):
    paragraphs = text.split("\n")
    chunks = []
    current_chunk = ""
    for para in paragraphs:
        if len(current_chunk) + len(para) < max_chars:
            current_chunk += para + "\n"
        else:
            chunks.append(current_chunk.strip())
            current_chunk = para + "\n"
    if current_chunk:
        chunks.append(current_chunk.strip())
    return chunks

# -------------------------------
# 🤖 Gerar questões com DeepSeek
# -------------------------------
def gerar_questoes_deepseek(texto):
    prompt = f"""
    Gere 5 questões de múltipla escolha baseadas no seguinte conteúdo:
    {texto}

    Formato de resposta em JSON (uma lista de objetos):
    [
      {{
        "pergunta": "texto da questão",
        "opcoes": ["A) ...", "B) ...", "C) ...", "D) ..."],
        "resposta_correta": "A",
        "justificativa": "explicação curta baseada no texto"
      }}
    ]
    """
    try:
        response = deepseek_client.chat.completions.create(
            model="tngtech/deepseek-r1t2-chimera:free",
            messages=[{"role": "user", "content": prompt}],
        )
        content = response.choices[0].message.content
        
        # Tenta limpar o JSON (às vezes a IA adiciona markdown)
        if content.strip().startswith("```json"):
            content = content.strip().lstrip("```json").rstrip("```")
            
        return json.loads(content)
    except json.JSONDecodeError:
        st.warning(f"Não foi possível decodificar a resposta da IA (DeepSeek). Tentando novamente...")
        return [] # Retorna lista vazia em caso de erro
    except Exception as e:
        st.error(f"Erro na API DeepSeek: {e}")
        return []

# -------------------------------
# 🧹 Refinar questões com Llama
# -------------------------------
def refinar_questoes_llama(questoes):
    if not questoes:
        return []
    
    prompt = f"""
    Revise as seguintes questões, corrija inconsistências e melhore clareza e gramática.
    Mantenha o formato JSON idêntico.

    Questões:
    {json.dumps(questoes, ensure_ascii=False, indent=2)}
    """
    try:
        response = llama_client.chat.completions.create(
            model="meta-llama/llama-3.3-70b-instruct:free",
            messages=[{"role": "user", "content": prompt}],
        )
        content = response.choices[0].message.content

        if content.strip().startswith("```json"):
            content = content.strip().lstrip("```json").rstrip("```")

        return json.loads(content)
    except json.JSONDecodeError:
        st.warning("Não foi possível decodificar a resposta do Llama. Usando as questões originais.")
        return questoes # Retorna original em caso de erro de refino
    except Exception as e:
        st.error(f"Erro na API Llama: {e}")
        return questoes

# -------------------------------
# 💾 Salvar no Supabase
# -------------------------------
def salvar_quiz(disciplina, nome, questoes):
    try:
        data = {
            "id": str(uuid.uuid4()),
            "nome": nome,
            "disciplina": disciplina,
            "questoes": json.dumps(questoes), # Salva como string JSON
        }
        supabase.table("quizzes").insert(data).execute()
        return True
    except Exception as e:
        st.error(f"Erro ao salvar no Supabase: {e}")
        return False

# -------------------------------
# 🎯 Função reutilizável: Responder Quiz
# -------------------------------
def render_quiz_taker(questoes, is_temp=False):
    """
    Renderiza a interface para responder um quiz.
    'is_temp' é True se o quiz não foi salvo (veio do "Responder Agora").
    """
    
    # Garante que 'questoes' é uma lista de dicionários
    if isinstance(questoes, str):
        try:
            questoes_list = json.loads(questoes)
        except json.JSONDecodeError:
            st.error("Formato de quiz inválido.")
            return
    elif isinstance(questoes, list):
        questoes_list = questoes
    else:
        st.error("Formato de quiz desconhecido.")
        return

    if not questoes_list:
        st.warning("Este quiz não contém questões.")
        return

    st.subheader("Responda as questões:")
    
    for i, q in enumerate(questoes_list):
        if not isinstance(q, dict) or "pergunta" not in q or "opcoes" not in q:
            st.warning(f"Ignorando questão {i+1} (formato inválido).")
            continue

        st.write(f"**{i+1}. {q['pergunta']}**")
        
        # Garante que as opções são uma lista
        opcoes = q.get("opcoes", [])
        if not isinstance(opcoes, list) or not opcoes:
             st.write("Esta questão não tem opções.")
             continue
             
        resposta = st.radio("Escolha uma opção:", opcoes, key=f"q{i}")
        
        if st.button(f"Verificar {i+1}", key=f"b{i}"):
            correta_prefix = q.get("resposta_correta", "Z") # "A", "B", etc.
            
            # Encontra o texto completo da resposta correta
            correta_full = next((opt for opt in opcoes if opt.strip().startswith(correta_prefix)), "N/A")

            if resposta.strip().startswith(correta_prefix):
                st.success("✅ Correto!")
            else:
                st.error(f"❌ Incorreto. Resposta correta: {correta_prefix}")
                if q.get("justificativa"):
                    st.info(f"**Justificativa:** {q['justificativa']}")
                
                # Log de erros para a aba "Revisão de erros"
                error_entry = {
                    "pergunta": q["pergunta"],
                    "sua_resposta": resposta,
                    "resposta_correta": correta_full,
                    "justificativa": q.get("justificativa", "N/A")
                }
                if error_entry not in st.session_state.error_log:
                    st.session_state.error_log.append(error_entry)
        
        st.divider()

    if is_temp:
        if st.button("Voltar para Home"):
            st.session_state.quiz_to_take = None
            st.rerun()

# -------------------------------
# 🏠 Página Home (Geração)
# -------------------------------
def render_home_page():

    # Estado 0: Se o usuário clicou em "Responder Agora"
    if st.session_state.quiz_to_take:
        st.header("🎯 Responder Quiz (Temporário)")
        render_quiz_taker(st.session_state.quiz_to_take, is_temp=True)
        return

    st.title("🧠 QuizIA")
    st.subheader("Gere um quiz interativo com a IA")
    st.markdown("---")

    # Estado 3: Mostrar formulário para salvar
    if st.session_state.show_save_form:
        st.header("💾 Salvar Quiz")
        with st.form("save_form"):
            disciplina = st.text_input("Nome da Matéria *")
            nome_quiz = st.text_input("Nome do Quiz *")
            submitted = st.form_submit_button("Confirmar e Salvar")
            
            if submitted:
                if not disciplina or not nome_quiz:
                    st.error("Por favor, preencha todos os campos obrigatórios.")
                else:
                    questoes = st.session_state.generated_questions
                    if salvar_quiz(disciplina, nome_quiz, questoes):
                        st.success(f"Quiz '{nome_quiz}' salvo com sucesso! Acesse-o na aba 'Disciplinas'.")
                        st.session_state.generated_questions = None
                        st.session_state.show_save_form = False
                        st.rerun()
                    # Se salvar falhar, o 'salvar_quiz' já mostra o erro
        
        if st.button("Cancelar"):
            st.session_state.show_save_form = False
            st.rerun()

    # Estado 2: Mostrar questões geradas e opções
    elif st.session_state.generated_questions:
        st.header("Questões Geradas com Sucesso!")
        st.json(st.session_state.generated_questions)
        st.markdown("---")
        
        col1, col2 = st.columns(2)
        if col1.button("💾 Salvar Quiz", use_container_width=True):
            st.session_state.show_save_form = True
            st.rerun()
        if col2.button("🎯 Responder Agora (Sem Salvar)", use_container_width=True):
            st.session_state.quiz_to_take = st.session_state.generated_questions
            st.session_state.generated_questions = None # Limpa para o próximo
            st.rerun()

    # Estado 1: Mostrar opções de entrada (Upload/Texto)
    else:
        col1, col2 = st.columns([1, 1])
        with col1:
            if st.button("⬆️ Upload do Arquivo", use_container_width=True):
                st.session_state.input_method = "upload"
        with col2:
            if st.button("⌨️ Inserir o Texto", use_container_width=True):
                st.session_state.input_method = "text"
        
        st.markdown("---")

        uploaded_file = None
        texto_manual = ""

        if st.session_state.input_method == "upload":
            uploaded_file = st.file_uploader("Envie um PDF", type=["pdf"], label_visibility="visible")
        
        if st.session_state.input_method == "text":
            texto_manual = st.text_area("Cole o conteúdo aqui", height=250, placeholder="Insira seu texto...")

        if st.session_state.input_method and st.button("🚀 Gerar Questões"):
            with st.spinner("Gerando questões com IA... (Isso pode levar um tempo)"):
                texto = ""
                if uploaded_file:
                    texto = extract_text_from_pdf(uploaded_file)
                elif texto_manual:
                    texto = texto_manual
                else:
                    st.warning("Envie um PDF ou insira texto!")
                    st.stop()
                
                if not texto:
                    st.error("Não foi possível extrair texto do conteúdo.")
                    st.stop()

                chunks = chunk_text(texto)
                questoes_final = []

                progress_bar = st.progress(0, text="Processando partes do texto...")

                for i, chunk in enumerate(chunks):
                    st.info(f"🔹 Processando parte {i+1}/{len(chunks)}...")
                    q = gerar_questoes_deepseek(chunk)
                    
                    if q: # Só refina se o deepseek retornar algo
                        q_refinado = refinar_questoes_llama(q)
                        questoes_final.extend(q_refinado)
                    
                    progress_bar.progress((i + 1) / len(chunks), text=f"Parte {i+1}/{len(chunks)} processada")
                    time.sleep(1) # Evita sobrecarga da API

                if questoes_final:
                    st.session_state.generated_questions = questoes_final
                    st.session_state.input_method = None # Reseta o método de input
                    st.rerun()
                else:
                    st.error("Não foi possível gerar nenhuma questão. Verifique o conteúdo ou tente novamente.")

# -------------------------------
# 📚 Página Disciplinas (Responder Salvo)
# -------------------------------
def render_disciplinas_page():
    st.header("📚 Minhas Disciplinas e Quizzes")

    try:
        quizzes = supabase.table("quizzes").select("id, nome, disciplina").execute()
        
        if not quizzes.data:
            st.warning("Nenhum quiz salvo encontrado.")
            return

        # Agrupa por disciplina
        disciplinas = {}
        for q in quizzes.data:
            disc = q["disciplina"]
            if disc not in disciplinas:
                disciplinas[disc] = []
            disciplinas[disc].append({"id": q["id"], "nome": q["nome"]})

        # Cria abas para cada disciplina
        tabs = st.tabs(disciplinas.keys())
        
        for i, tab in enumerate(tabs):
            disciplina_nome = list(disciplinas.keys())[i]
            with tab:
                st.subheader(f"Quizzes de {disciplina_nome}")
                for quiz in disciplinas[disciplina_nome]:
                    if st.button(quiz["nome"], key=f"quiz_{quiz['id']}"):
                        st.session_state.selected_quiz_id = quiz["id"]
        
        # Se um quiz foi selecionado, busca e mostra
        if "selected_quiz_id" in st.session_state:
            quiz_id = st.session_state.selected_quiz_id
            st.divider()
            st.subheader(f"Respondendo Quiz")

            if st.button("Voltar para lista"):
                del st.session_state.selected_quiz_id
                st.rerun()

            # Busca o quiz completo
            quiz_data = supabase.table("quizzes").select("nome, questoes").eq("id", quiz_id).single().execute()
            
            if quiz_data.data:
                st.markdown(f"**Quiz:** {quiz_data.data['nome']}")
                render_quiz_taker(quiz_data.data["questoes"], is_temp=False)
            else:
                st.error("Não foi possível carregar o quiz selecionado.")

    except Exception as e:
        st.error(f"Erro ao buscar quizzes: {e}")

# -------------------------------
# ❌ Página Revisão de Erros
# -------------------------------
def render_revisao_page():
    st.header("❌ Revisão de Erros")
    
    if not st.session_state.error_log:
        st.info("Você ainda não errou nenhuma questão. Ótimo trabalho!")
        return

    if st.button("Limpar Histórico de Erros"):
        st.session_state.error_log = []
        st.rerun()

    st.subheader("Questões que você errou:")
    
    for i, erro in enumerate(st.session_state.error_log):
        with st.container(border=True):
            st.markdown(f"**{i+1}. {erro['pergunta']}**")
            st.error(f"Sua resposta: {erro['sua_resposta']}")
            st.success(f"Resposta correta: {erro['resposta_correta']}")
            st.info(f"Justificativa: {erro['justificativa']}")
        st.divider()

# -------------------------------
# 🗂️ Página Flashcards (Placeholder)
# -------------------------------
def render_flashcards_page():
    st.header("🗂️ Flashcards")
    st.info("Em construção... 🏗️")
    st.write("Esta seção permitirá revisar os conceitos das questões erradas em formato de flashcards.")

# -------------------------------
# ⚙️ Página Configurar (Placeholder)
# -------------------------------
def render_configurar_page():
    st.header("⚙️ Configurar Estilo e Dificuldade")
    st.info("Em construção... 🏗️")
    st.write("Aqui você poderá ajustar a dificuldade das questões (ex: mais difíceis, conceituais) e o estilo (ex: 'complete a lacuna', 'verdadeiro ou falso').")


# -------------------------------
# 🧩 Interface Principal Streamlit
# -------------------------------
st.set_page_config(page_title="QuizIA", layout="wide")

# -------------------------------
# 💾 Inicialização do st.session_state
# -------------------------------
if "page" not in st.session_state:
    st.session_state.page = "Home"
if "input_method" not in st.session_state:
    st.session_state.input_method = None
if "generated_questions" not in st.session_state:
    st.session_state.generated_questions = None
if "show_save_form" not in st.session_state:
    st.session_state.show_save_form = False
if "quiz_to_take" not in st.session_state:
    st.session_state.quiz_to_take = None
if "error_log" not in st.session_state:
    st.session_state.error_log = []
if "selected_quiz_id" not in st.session_state:
    st.session_state.selected_quiz_id = None


# -------------------------------
# 🧭 Navegação Sidebar
# -------------------------------
with st.sidebar:
    st.title("Menu QuizIA")
    
    if st.button("🏠 Home", use_container_width=True):
        # Reseta o estado da Home ao clicar
        st.session_state.page = "Home"
        st.session_state.input_method = None
        st.session_state.generated_questions = None
        st.session_state.show_save_form = False
        st.session_state.quiz_to_take = None
        st.session_state.selected_quiz_id = None
        st.rerun()

    if st.button("📚 Disciplinas", use_container_width=True):
        st.session_state.page = "Disciplinas"
        st.session_state.selected_quiz_id = None # Reseta quiz selecionado
        st.rerun()

    if st.button("❌ Revisão de erros", use_container_width=True):
        st.session_state.page = "Revisão de erros"
        st.rerun()

    if st.button("🗂️ Flashcards", use_container_width=True):
        st.session_state.page = "Flashcards"
        st.rerun()

    if st.button("⚙️ Configurar", use_container_width=True):
        st.session_state.page = "Configurar"
        st.rerun()

# -------------------------------
# 🚦 Roteador de Páginas
# -------------------------------
if st.session_state.page == "Home":
    render_home_page()
elif st.session_state.page == "Disciplinas":
    render_disciplinas_page()
elif st.session_state.page == "Revisão de erros":
    render_revisao_page()
elif st.session_state.page == "Flashcards":
    render_flashcards_page()
elif st.session_state.page == "Configurar":
    render_configurar_page()
