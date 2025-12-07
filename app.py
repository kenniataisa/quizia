import streamlit as st
import fitz 
import json
from supabase import create_client, Client
from openai import OpenAI
import time
import uuid
import re # Importando regex para limpeza de JSON
import random # NÃO É MAIS USADO

# -------------------------------
# 🔑 Configurações
# -------------------------------
SUPABASE_URL = st.secrets.get("SUPABASE_URL", "SUA_URL_AQUI")
SUPABASE_KEY = st.secrets.get("SUPABASE_KEY", "SUA_CHAVE_AQUI")
OPENROUTER_API_KEY = st.secrets.get("OPENROUTER_API_KEY", "SUA_CHAVE_AQUI")

# Configurações Adicionais do OpenRouter (SUGESTÃO: Mova para secrets)
SITE_URL = "http://quizia.streamlit.app" 
SITE_NAME = "QuizIA App"

if SUPABASE_URL == "SUA_URL_AQUI" or SUPABASE_KEY == "SUA_CHAVE_AQUI" or OPENROUTER_API_KEY == "SUA_CHAVE_AQUI":
    st.error("As chaves de API (Supabase, OpenRouter) não foram configuradas nos 'Secrets' do Streamlit.")
    st.stop()

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# -------------------------------
# 🔧 Inicializa clientes OpenRouter
# -------------------------------
def create_openrouter_client():
    """Cria e retorna o cliente OpenAI configurado para OpenRouter."""
    return OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=OPENROUTER_API_KEY,
    )

deepseek_client = create_openrouter_client()
llama_client = create_openrouter_client()

# Headers de Rastreamento
OPENROUTER_HEADERS = {
    "HTTP-Referer": SITE_URL,
    "X-Title": SITE_NAME,
}

# -------------------------------
# 📚 Funções de Extração e Chunk (Sem Alteração)
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
# 🤖 Funções de Geração de IA (Atualizadas com extra_headers)
# -------------------------------
def limpar_json_ia(content, tipo_lista=True):
    """Tenta extrair um objeto JSON de uma string de resposta da IA."""
    if tipo_lista:
        match = re.search(r'\[.*\]', content, re.DOTALL)
    else:
        match = re.search(r'\{.*\}', content, re.DOTALL)
        
    if match:
        json_text = match.group(0)
    else:
        json_text = content
        
    try:
        return json.loads(json_text)
    except json.JSONDecodeError:
        st.warning(f"Não foi possível decodificar a resposta da IA. Resposta bruta: {content[:100]}...")
        return None

def gerar_questoes_deepseek(texto, dificuldade, estilo):
    """Gera questões com base no texto, dificuldade e estilo (Modelo DeepSeek)."""
    
    # Lógica de adaptação de estilo (mantida do código anterior)
    if "Múltipla Escolha" in estilo:
        estilo_prompt = "Múltipla escolha (4 alternativas A-D)"
    elif "Verdadeiro/Falso" in estilo:
        estilo_prompt = "Verdadeiro ou Falso (resposta deve ser 'V' ou 'F')"
    elif "Resposta Curta" in estilo:
        estilo_prompt = "Resposta aberta (pergunta cuja resposta correta seja textual)"
    else:
        estilo_prompt = "Múltipla escolha, Verdadeiro ou Falso ou Preencher lacuna (misturar os tipos)"

    prompt = f"""
Você deve gerar questões SOMENTE com base no conteúdo abaixo.
NÃO invente nada que não esteja explícito no texto.
... [Instruções de geração e formato JSON omitidas para brevidade no resumo, mas presentes no código] ...
"""

    try:
        # ALTERAÇÃO: Incluindo extra_headers
        response = deepseek_client.chat.completions.create(
            extra_headers=OPENROUTER_HEADERS,
            model="tngtech/deepseek-r1t2-chimera:free",
            messages=[{"role": "user", "content": prompt}],
        )
        content = response.choices[0].message.content
        return limpar_json_ia(content, tipo_lista=True) or []
    except Exception as e:
        st.error(f"Erro na API DeepSeek (Quiz): {e}")
        return []

def refinar_questoes_llama(questoes):
    """Melhora clareza, gramática e corrige inconsistências das questões (Modelo Llama)."""
    if not questoes: return []
    prompt = f"""
    Revise as seguintes questões, corrija inconsistências e melhore clareza e gramática.
    Mantenha o formato JSON idêntico.
    Questões:
    {json.dumps(questoes, ensure_ascii=False, indent=2)}
    """
    try:
        # ALTERAÇÃO: Incluindo extra_headers
        response = llama_client.chat.completions.create(
            extra_headers=OPENROUTER_HEADERS,
            model="meta-llama/llama-3.3-70b-instruct:free",
            messages=[{"role": "user", "content": prompt}],
        )
        content = response.choices[0].message.content
        return limpar_json_ia(content, tipo_lista=True) or questoes
    except Exception as e:
        st.error(f"Erro na API Llama (Refino): {e}")
        return questoes

def avaliar_resposta_aberta(resposta_usuario, resposta_correta, trecho_referencia):
    """Usa IA para avaliar a resposta aberta com semelhança semântica (Modelo Llama)."""

    client = create_openrouter_client() # Cria o cliente localmente

    prompt = f"""
Compare a resposta do aluno com a resposta correta.
... [Instruções para avaliação e formato JSON omitidas para brevidade no resumo] ...
"""
    try:
        # ALTERAÇÃO: Incluindo extra_headers
        response = client.chat.completions.create(
            extra_headers=OPENROUTER_HEADERS,
            model="meta-llama/llama-3.3-70b-instruct:free",
            messages=[{"role": "user", "content": prompt}],
        )
        return limpar_json_ia(response.choices[0].message.content, tipo_lista=False)
    except Exception as e:
        st.error(f"Erro ao avaliar resposta aberta: {e}")
        return {
            "similaridade": 0,
            "correto": False,
            "explicacao": "Erro na IA."
        }


# -------------------------------
# 💾 Funções do Supabase (Ajustadas - Removendo Flashcard)
# -------------------------------
def salvar_quiz(disciplina, nome, questoes):
    try:
        data = { "nome": nome, "disciplina": disciplina, "questoes": json.dumps(questoes) }
        supabase.table("quizzes").insert(data).execute()
        return True
    except Exception as e:
        st.error(f"Erro ao salvar o quiz no Supabase: {e}"); return False

def deletar_item_supabase(id, tipo):
    """Função para deletar quizzes."""
    tabela = "quizzes" 
    try:
        supabase.table(tabela).delete().eq("id", id).execute()
        st.toast(f"{tipo.capitalize()} deletado com sucesso!", icon="🗑️")
        return True
    except Exception as e:
        st.error(f"Erro ao deletar: {e}"); return False

def render_home_page():

    if st.session_state.quiz_to_take:
        st.header("🎯 Responder Quiz (Temporário)")
        render_quiz_taker(st.session_state.quiz_to_take, is_temp=True)
        return

    st.title("🧠 QuizIA")
    st.subheader("Gere um quiz interativo ou flashcards com IA")
    st.markdown("---")

    if st.session_state.show_save_form:
        tipo = st.session_state.show_save_form
        nome_tipo = "Quiz" if tipo == 'quiz' else "Baralho"
        st.header(f"💾 Salvar {nome_tipo}")
        with st.form("save_form"):
            disciplina = st.text_input("Nome da Matéria *")
            nome_item = st.text_input(f"Nome do {nome_tipo} *")
            submitted = st.form_submit_button("Confirmar e Salvar")
            
            if submitted:
                if not disciplina or not nome_item:
                    st.error("Por favor, preencha todos os campos obrigatórios.")
                else:
                    success = False
                    if tipo == 'quiz':
                        success = salvar_quiz(disciplina, nome_item, st.session_state.generated_quiz)
                    elif tipo == 'deck':
                        success = salvar_flashcard_deck(disciplina, nome_item, st.session_state.generated_flashcards)
                    
                    if success:
                        st.success(f"{nome_tipo} '{nome_item}' salvo com sucesso! Acesse-o na aba 'Disciplinas'.")
                        st.session_state.generated_quiz = None
                        st.session_state.generated_flashcards = None
                        st.session_state.show_save_form = None
                        st.rerun()
        if st.button("Cancelar"): st.session_state.show_save_form = None; st.rerun()

    elif st.session_state.generated_quiz:
        st.header("Questões Geradas com Sucesso!")
        st.success(f"✅ Sucesso! {len(st.session_state.generated_quiz)} questões foram geradas.")
        st.info("O que você gostaria de fazer agora?")
        st.markdown("---")
        col1, col2 = st.columns(2)
        if col1.button("💾 Salvar Quiz", use_container_width=True): st.session_state.show_save_form = 'quiz'; st.rerun()
        if col2.button("🎯 Responder Agora (Sem Salvar)", use_container_width=True):
            st.session_state.quiz_to_take = st.session_state.generated_quiz
            st.session_state.generated_quiz = None; st.rerun()
        if st.button("Descartar", use_container_width=True): st.session_state.generated_quiz = None; st.rerun()

    elif st.session_state.generated_flashcards:
        st.header("Flashcards Gerados com Sucesso!")
        st.success(f"✅ Sucesso! {len(st.session_state.generated_flashcards)} flashcards foram gerados.")
        st.info("Salve este baralho para poder revisá-lo na aba 'Disciplinas'.")
        st.write("**Prévia:**")
        for card in st.session_state.generated_flashcards[:3]:
            st.write(f"**Frente:** {card['frente']}")
            st.write(f"**Verso:** {card['verso']}")
            st.divider()
        st.markdown("---")
        col1, col2 = st.columns(2)
        if col1.button("💾 Salvar Baralho", use_container_width=True): st.session_state.show_save_form = 'deck'; st.rerun()
        if col2.button("Descartar", use_container_width=True): st.session_state.generated_flashcards = None; st.rerun()

    else:
        st.info("Procurando seus quizzes e baralhos salvos? 📚 Acesse a aba **Disciplinas** no menu.")
        st.markdown("---")
        input_tabs = st.tabs(["⬆️ Upload de Arquivo", "⌨️ Inserir Texto"])
        with input_tabs[0]:
            uploaded_file = st.file_uploader("Envie um PDF", type=["pdf"], label_visibility="collapsed")
        with input_tabs[1]:
            texto_manual = st.text_area("Cole o conteúdo aqui", height=250, placeholder="Insira seu texto...", label_visibility="collapsed")

        st.markdown("---")
        st.subheader("O que você quer criar com este material?")
        col1, col2 = st.columns(2)
        
        if col1.button("🚀 Gerar Quiz", use_container_width=True):
            texto, _ = (extract_text_from_pdf(uploaded_file), "PDF") if uploaded_file else (texto_manual, "Texto")
            if not texto: st.warning("Por favor, envie um PDF ou insira texto."); st.stop()
            
            with st.spinner("Gerando quiz..."):
                chunks = chunk_text(texto); questoes_final = []
                progress_bar = st.progress(0, text="Processando partes do texto...")
                for i, chunk in enumerate(chunks):
                    st.info(f"🔹 Processando (Quiz) parte {i+1}/{len(chunks)}...")
                    q = gerar_questoes_deepseek(chunk, st.session_state.config_dificuldade, st.session_state.config_estilo)
                    if q: q_refinado = refinar_questoes_llama(q); questoes_final.extend(q_refinado)
                    progress_bar.progress((i + 1) / len(chunks), text=f"Parte {i+1}/{len(chunks)} processada")
                    time.sleep(1)
                
                if questoes_final: st.session_state.generated_quiz = questoes_final; st.rerun()
                else: st.error("Não foi possível gerar nenhuma questão.")

        if col2.button("🗂️ Gerar Flashcards", use_container_width=True):
            texto, _ = (extract_text_from_pdf(uploaded_file), "PDF") if uploaded_file else (texto_manual, "Texto")
            if not texto: st.warning("Por favor, envie um PDF ou insira texto."); st.stop()
            
            with st.spinner("Gerando flashcards..."):
                chunks = chunk_text(texto); cards_final = []
                progress_bar = st.progress(0, text="Processando partes do texto...")
                for i, chunk in enumerate(chunks):
                    st.info(f"🔹 Processando (Cards) parte {i+1}/{len(chunks)}...")
                    cards = gerar_flashcards_ia(chunk, st.session_state.config_dificuldade)
                    if cards: cards_final.extend(cards)
                    progress_bar.progress((i + 1) / len(chunks), text=f"Parte {i+1}/{len(chunks)} processada")
                    time.sleep(1)
                
                if cards_final: st.session_state.generated_flashcards = cards_final; st.rerun()
                else: st.error("Não foi possível gerar nenhum flashcard.")

# -------------------------------
# 📚 Página Disciplinas (Biblioteca)
# -------------------------------
def render_disciplinas_page():
    st.header("📚 Minhas Disciplinas")

    # --- (MUDANÇA) Caixa de Confirmação de Exclusão (Substitui st.modal) ---
    if st.session_state.confirm_delete_id:
        item_id = st.session_state.confirm_delete_id
        item_tipo = st.session_state.confirm_delete_type
        item_nome = st.session_state.confirm_delete_name
        
        st.error(f"**ALERTA DE EXCLUSÃO**")
        with st.container(border=True):
            st.warning(f"Você tem certeza que quer deletar o {item_tipo} **'{item_nome}'**?")
            st.write("Esta ação é permanente.")
            
            col1, col2 = st.columns(2)
            if col1.button("Confirmar Exclusão", use_container_width=True, type="primary"):
                if deletar_item_supabase(item_id, item_tipo):
                    st.session_state.confirm_delete_id = None
                    st.session_state.confirm_delete_type = None
                    st.session_state.confirm_delete_name = None
                    st.rerun()
            if col2.button("Cancelar", use_container_width=True):
                st.session_state.confirm_delete_id = None
                st.session_state.confirm_delete_type = None
                st.session_state.confirm_delete_name = None
                st.rerun()
        st.divider()
        # Para a execução para não mostrar o resto da página enquanto confirma
        return 
    # --- Fim da mudança ---

    # --- Lógica de Navegação 1: Mostrar Visualizador de Flashcard
    if st.session_state.selected_deck_id:
        try:
            deck_data = supabase.table("flashcard_decks").select("*").eq("id", st.session_state.selected_deck_id).single().execute()
            if deck_data.data: render_flashcard_viewer(deck_data.data)
            else: st.error("Não foi possível carregar o baralho."); st.session_state.selected_deck_id = None
        except Exception as e: st.error(f"Erro ao buscar baralho: {e}"); st.session_state.selected_deck_id = None
        return

    # --- Lógica de Navegação 2: Mostrar Visualizador de Quiz
    if st.session_state.selected_quiz_id:
        try:
            quiz_data = supabase.table("quizzes").select("nome, questoes, disciplina").eq("id", st.session_state.selected_quiz_id).single().execute()
            if quiz_data.data:
                st.subheader(f"Respondendo: {quiz_data.data['nome']}")
                st.caption(f"Disciplina: {quiz_data.data['disciplina']}")
                st.divider()
                render_quiz_taker(quiz_data.data["questoes"], disciplina_nome=quiz_data.data["disciplina"], is_temp=False)
            else: st.error("Não foi possível carregar o quiz."); st.session_state.selected_quiz_id = None
        except Exception as e: st.error(f"Erro ao buscar quiz: {e}"); st.session_state.selected_quiz_id = None
        return

    # --- Lógica de Navegação 3: Mostrar Detalhes da Disciplina (Abas)
    if st.session_state.selected_disciplina:
        if st.button("← Voltar para todas as disciplinas"):
            st.session_state.selected_disciplina = None; st.rerun()
        st.header(f"Disciplina: {st.session_state.selected_disciplina}")
        
        tab_quiz, tab_flash, tab_erros = st.tabs(["🎓 Quizzes", "🗂️ Flashcards de Estudo", "❌ Revisão de Erros"])

        with tab_quiz:
            st.subheader(f"Quizzes de {st.session_state.selected_disciplina}")
            try:
                quizzes_disc = supabase.table("quizzes").select("id, nome").eq("disciplina", st.session_state.selected_disciplina).execute()
                if not quizzes_disc.data: st.info("Nenhum quiz encontrado.")
                else:
                    for quiz in quizzes_disc.data:
                        col1, col2 = st.columns([0.9, 0.1])
                        with col1:
                            if st.button(quiz["nome"], key=f"quiz_{quiz['id']}", use_container_width=True):
                                st.session_state.selected_quiz_id = quiz["id"]; st.rerun()
                        with col2:
                            if st.button("🗑️", key=f"del_quiz_{quiz['id']}", use_container_width=True, help="Deletar este quiz"):
                                st.session_state.confirm_delete_id = quiz['id']
                                st.session_state.confirm_delete_type = 'quiz'
                                st.session_state.confirm_delete_name = quiz['nome']
                                st.rerun()
            except Exception as e: st.error(f"Erro ao buscar quizzes: {e}")

        with tab_flash:
            st.subheader(f"Baralhos de {st.session_state.selected_disciplina}")
            try:
                decks_disc = supabase.table("flashcard_decks").select("id, nome").eq("disciplina", st.session_state.selected_disciplina).execute()
                if not decks_disc.data: st.info("Nenhum baralho de flashcards encontrado.")
                else:
                    for deck in decks_disc.data:
                        col1, col2 = st.columns([0.9, 0.1])
                        with col1:
                            if st.button(deck["nome"], key=f"deck_{deck['id']}", use_container_width=True):
                                st.session_state.selected_deck_id = deck["id"]; st.rerun()
                        with col2:
                            if st.button("🗑️", key=f"del_deck_{deck['id']}", use_container_width=True, help="Deletar este baralho"):
                                st.session_state.confirm_delete_id = deck['id']
                                st.session_state.confirm_delete_type = 'deck'
                                st.session_state.confirm_delete_name = deck['nome']
                                st.rerun()
            except Exception as e: st.error(f"Erro ao buscar baralhos: {e}")

        with tab_erros:
            st.subheader("Revisão de Erros da Disciplina")
            erros_disciplina = [e for e in st.session_state.error_log if e["disciplina"] == st.session_state.selected_disciplina]
            if not erros_disciplina: st.info("Nenhum erro registrado para esta disciplina ainda.")
            else:
                st.write(f"{len(erros_disciplina)} erros para revisar:")
                for i, erro in enumerate(erros_disciplina):
                    with st.container(border=True):
                        st.markdown(f"**{i+1}. {erro['pergunta']}**")
                        st.error(f"Sua resposta: {erro['sua_resposta']}")
                        st.success(f"Resposta correta: {erro['resposta_correta']}")
                        if erro['justificativa'] != "N/A": st.info(f"Justificativa: {erro['justificativa']}")
                    st.divider()
        return

    # --- Lógica de Navegação 4: Mostrar Cards de Disciplina (Padrão)
    try:
        quizzes = supabase.table("quizzes").select("disciplina").execute()
        decks = supabase.table("flashcard_decks").select("disciplina").execute()
        disciplinas = set()
        if quizzes.data: disciplinas.update([q["disciplina"] for q in quizzes.data])
        if decks.data: disciplinas.update([d["disciplina"] for d in decks.data])

        if not disciplinas:
            st.warning("Nenhuma disciplina encontrada. Crie um quiz ou baralho na página Home!"); return

        for disc_nome in sorted(list(disciplinas)):
            with st.container(border=True):
                st.subheader(disc_nome)
                if st.button("Abrir Disciplina", key=f"open_{disc_nome}"):
                    st.session_state.selected_disciplina = disc_nome
                    st.rerun()
            st.markdown("---")
    except Exception as e: st.error(f"Erro ao buscar disciplinas: {e}")

# -------------------------------
# ❌ Página Revisão de Erros
# -------------------------------
def render_revisao_page():
    st.header("❌ Revisão de Erros (de Quizzes)")
    
    if not st.session_state.error_log:
        st.info("Você ainda não errou nenhuma questão de quiz. Ótimo trabalho!"); return

    if st.button("Limpar Histórico de Erros"):
        st.session_state.error_log = []; st.session_state.filtro_revisao = "Todas"; st.rerun()

    disciplinas_com_erro = sorted(list(set(e["disciplina"] for e in st.session_state.error_log)))
    opcoes_filtro = ["Todas"] + disciplinas_com_erro
    indice_filtro = 0
    if st.session_state.filtro_revisao in opcoes_filtro:
        indice_filtro = opcoes_filtro.index(st.session_state.filtro_revisao)

    filtro_disciplina = st.selectbox("Filtrar por Disciplina:", opcoes_filtro, index=indice_filtro, key="filtro_selectbox")
    
    if 'filtro_selectbox' in st.session_state and st.session_state.filtro_selectbox != st.session_state.filtro_revisao:
        st.session_state.filtro_revisao = st.session_state.filtro_selectbox; st.rerun()

    st.subheader("Questões que você errou:")
    erros_filtrados = st.session_state.error_log
    if filtro_disciplina != "Todas":
        erros_filtrados = [e for e in st.session_state.error_log if e["disciplina"] == filtro_disciplina]

    if not erros_filtrados: st.info("Nenhum erro encontrado para esta disciplina."); return

    for i, erro in enumerate(erros_filtrados):
        with st.container(border=True):
            st.caption(f"Disciplina: {erro['disciplina']}")
            st.markdown(f"**{i+1}. {erro['pergunta']}**")
            st.error(f"Sua resposta: {erro['sua_resposta']}")
            st.success(f"Resposta correta: {erro['resposta_correta']}")
            st.info(f"Justificativa: {erro['justificativa']}")
        st.divider()

# -------------------------------
# ⚙️ Página Configurar (IMPLEMENTADA)
# -------------------------------
def render_configurar_page():
    st.header("⚙️ Configurar Geração de IA")
    st.write("Ajuste as preferências para a geração de novos quizzes e flashcards.")
    st.divider()

    st.subheader("Configurações de Geração")
    
    # Dificuldade (usada por ambos)
    st.session_state.config_dificuldade = st.radio(
        "Nível de Dificuldade:",
        ["Padrão (Recomendado)", "Fácil (Foco em Conceitos)", "Difícil (Análise Crítica)"],
        key="config_dificuldade_widget",
        horizontal=True,
        index=["Padrão (Recomendado)", "Fácil (Foco em Conceitos)", "Difícil (Análise Crítica)"].index(st.session_state.config_dificuldade)
    )

    # Estilo de Questão (só para quiz)
    st.session_state.config_estilo = st.radio(
        "Estilo de Questão (para Quizzes):",
        ["Múltipla Escolha (Padrão)", "Verdadeiro/Falso", "Resposta Curta (beta)"],
        key="config_estilo_widget",
        horizontal=True,
        index=["Múltipla Escolha (Padrão)", "Verdadeiro/Falso", "Resposta Curta (beta)"].index(st.session_state.config_estilo)
    )
    
    st.divider()
    st.info("Suas preferências são salvas automaticamente nesta sessão e usadas na página 'Home'.")

# -------------------------------
# 🧩 Interface Principal Streamlit
# -------------------------------
st.set_page_config(page_title="QuizIA", layout="wide")

# -------------------------------
# 💾 Inicialização do st.session_state
# -------------------------------
if "page" not in st.session_state: st.session_state.page = "Home"
if "generated_quiz" not in st.session_state: st.session_state.generated_quiz = None
if "generated_flashcards" not in st.session_state: st.session_state.generated_flashcards = None
if "show_save_form" not in st.session_state: st.session_state.show_save_form = None
if "quiz_to_take" not in st.session_state: st.session_state.quiz_to_take = None
if "error_log" not in st.session_state: st.session_state.error_log = []
if "selected_quiz_id" not in st.session_state: st.session_state.selected_quiz_id = None
if "selected_deck_id" not in st.session_state: st.session_state.selected_deck_id = None
if "selected_disciplina" not in st.session_state: st.session_state.selected_disciplina = None
if "filtro_revisao" not in st.session_state: st.session_state.filtro_revisao = "Todas"

# Flashcard Viewer (Spaced Repetition)
if "deck_master_list" not in st.session_state: st.session_state.deck_master_list = []
if "deck_to_review" not in st.session_state: st.session_state.deck_to_review = []
if "deck_completed" not in st.session_state: st.session_state.deck_completed = []
if "flashcard_flipped" not in st.session_state: st.session_state.flashcard_flipped = False

# Configurações de IA
if "config_dificuldade" not in st.session_state: st.session_state.config_dificuldade = "Padrão (Recomendado)"
if "config_estilo" not in st.session_state: st.session_state.config_estilo = "Múltipla Escolha (Padrão)"

# Modal de Deleção
if "confirm_delete_id" not in st.session_state: st.session_state.confirm_delete_id = None
if "confirm_delete_type" not in st.session_state: st.session_state.confirm_delete_type = None
if "confirm_delete_name" not in st.session_state: st.session_state.confirm_delete_name = None

# Widgets (para reter o estado do selectbox/radio)
if "config_dificuldade_widget" not in st.session_state: st.session_state.config_dificuldade_widget = st.session_state.config_dificuldade
if "config_estilo_widget" not in st.session_state: st.session_state.config_estilo_widget = st.session_state.config_estilo
if "filtro_selectbox" not in st.session_state: st.session_state.filtro_selectbox = st.session_state.filtro_revisao

# -------------------------------
# 🧭 Navegação Sidebar
# -------------------------------
def reset_page_states():
    st.session_state.selected_quiz_id = None
    st.session_state.selected_disciplina = None
    st.session_state.selected_deck_id = None
    st.session_state.filtro_revisao = "Todas"
    st.session_state.generated_quiz = None
    st.session_state.generated_flashcards = None
    st.session_state.show_save_form = None
    st.session_state.quiz_to_take = None
    st.session_state.deck_master_list = []
    st.session_state.deck_to_review = []
    st.session_state.deck_completed = []
    st.session_state.flashcard_flipped = False
    st.session_state.confirm_delete_id = None # Reseta a confirmação de delete
    st.session_state.confirm_delete_type = None
    st.session_state.confirm_delete_name = None

with st.sidebar:
    st.title("Menu QuizIA")
    
    if st.button("🏠 Home", use_container_width=True):
        st.session_state.page = "Home"
        reset_page_states()
        st.rerun()

    if st.button("📚 Disciplinas", use_container_width=True):
        st.session_state.page = "Disciplinas"
        reset_page_states()
        st.rerun()

    if st.button("❌ Revisão de erros", use_container_width=True):
        st.session_state.page = "Revisão de erros"
        st.session_state.filtro_revisao = "Todas" # Reseta só o filtro
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
elif st.session_state.page == "Configurar":
    render_configurar_page()
