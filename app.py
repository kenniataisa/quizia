import streamlit as st
import fitz  # PyMuPDF
import json
from supabase import create_client, Client
from openai import OpenAI
import time
import uuid
import re  # Importando regex para limpeza de JSON

# -------------------------------
# 🔑 Configurações
# -------------------------------
SUPABASE_URL = st.secrets.get("SUPABASE_URL", "SUA_URL_AQUI")
SUPABASE_KEY = st.secrets.get("SUPABASE_KEY", "SUA_CHAVE_AQUI")
OPENROUTER_API_KEY = st.secrets.get("OPENROUTER_API_KEY", "SUA_CHAVE_AQUI")

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
# 📚 Funções de Extração e Chunk
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
# 🤖 Funções de Geração de IA
# -------------------------------
def limpar_json_ia(content, tipo_lista=True):
    """Tenta extrair um objeto JSON de uma string de resposta da IA."""
    if tipo_lista:
        # Tenta encontrar uma lista JSON [...]
        match = re.search(r'\[.*\]', content, re.DOTALL)
    else:
        # Tenta encontrar um objeto JSON {...}
        match = re.search(r'\{.*\}', content, re.DOTALL)
        
    if match:
        json_text = match.group(0)
    else:
        # Fallback se não encontrar os colchetes/chaves
        json_text = content
        
    try:
        return json.loads(json_text)
    except json.JSONDecodeError:
        st.warning(f"Não foi possível decodificar a resposta da IA. Resposta bruta: {content[:100]}...")
        return None

def gerar_questoes_deepseek(texto):
    prompt = f"""
    Gere 5 questões de múltipla escolha baseadas no seguinte conteúdo:
    {texto}
    Formato de resposta em JSON (uma lista de objetos):
    [
      {{"pergunta": "...", "opcoes": ["A) ...", "B) ..."], "resposta_correta": "A", "justificativa": "..."}}
    ]
    """
    try:
        response = deepseek_client.chat.completions.create(
            model="tngtech/deepseek-r1t2-chimera:free",
            messages=[{"role": "user", "content": prompt}],
        )
        content = response.choices[0].message.content
        return limpar_json_ia(content, tipo_lista=True) or []
    except Exception as e:
        st.error(f"Erro na API DeepSeek (Quiz): {e}")
        return []

def refinar_questoes_llama(questoes):
    if not questoes: return []
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
        return limpar_json_ia(content, tipo_lista=True) or questoes
    except Exception as e:
        st.error(f"Erro na API Llama (Refino): {e}")
        return questoes

def gerar_flashcards_ia(texto):
    """NOVA FUNÇÃO para gerar flashcards."""
    prompt = f"""
    Gere 5 a 10 flashcards de "frente e verso" baseados no seguinte conteúdo.
    Foque em conceitos-chave, termos e definições.
    {texto}
    Formato de resposta em JSON (uma lista de objetos):
    [
      {{"frente": "Termo ou Pergunta Curta", "verso": "Definição ou Resposta Completa"}}
    ]
    """
    try:
        response = deepseek_client.chat.completions.create(
            model="tngtech/deepseek-r1t2-chimera:free",
            messages=[{"role": "user", "content": prompt}],
        )
        content = response.choices[0].message.content
        return limpar_json_ia(content, tipo_lista=True) or []
    except Exception as e:
        st.error(f"Erro na API DeepSeek (Flashcards): {e}")
        return []

# -------------------------------
# 💾 Funções do Supabase
# -------------------------------
def salvar_quiz(disciplina, nome, questoes):
    try:
        data = {
            "id": str(uuid.uuid4()),
            "nome": nome,
            "disciplina": disciplina,
            "questoes": json.dumps(questoes),
        }
        supabase.table("quizzes").insert(data).execute()
        return True
    except Exception as e:
        st.error(f"Erro ao salvar o quiz no Supabase: {e}")
        return False

def salvar_flashcard_deck(disciplina, nome, cards):
    """NOVA FUNÇÃO para salvar baralhos."""
    try:
        data = {
            "id": str(uuid.uuid4()),
            "nome": nome,
            "disciplina": disciplina,
            "cards": json.dumps(cards),
        }
        supabase.table("flashcard_decks").insert(data).execute()
        return True
    except Exception as e:
        st.error(f"Erro ao salvar o baralho no Supabase: {e}")
        st.info("Você criou a tabela 'flashcard_decks' conforme as instruções?")
        return False

# -------------------------------
# 🎯 Funções de Renderização de UI
# -------------------------------

def render_quiz_taker(questoes, disciplina_nome=None, is_temp=False):
    """Renderiza a interface para responder um quiz."""
    if isinstance(questoes, str):
        try: questoes_list = json.loads(questoes)
        except json.JSONDecodeError:
            st.error("Formato de quiz inválido."); return
    elif isinstance(questoes, list): questoes_list = questoes
    else: st.error("Formato de quiz desconhecido."); return

    if not questoes_list: st.warning("Este quiz não contém questões."); return

    st.subheader("Responda as questões:")
    for i, q in enumerate(questoes_list):
        if not isinstance(q, dict) or "pergunta" not in q or "opcoes" not in q:
            st.warning(f"Ignorando questão {i+1} (formato inválido)."); continue

        st.write(f"**{i+1}. {q['pergunta']}**")
        opcoes = q.get("opcoes", [])
        if not isinstance(opcoes, list) or not opcoes:
             st.write("Esta questão não tem opções."); continue
             
        resposta = st.radio("Escolha uma opção:", opcoes, key=f"q{i}")
        
        if st.button(f"Verificar {i+1}", key=f"b{i}"):
            correta_prefix = q.get("resposta_correta", "Z")
            correta_full = next((opt for opt in opcoes if opt.strip().startswith(correta_prefix)), "N/A")

            if resposta.strip().startswith(correta_prefix):
                st.success("✅ Correto!")
            else:
                st.error(f"❌ Incorreto. Resposta correta: {correta_prefix}")
                if q.get("justificativa"): st.info(f"**Justificativa:** {q['justificativa']}")
                
                error_entry = {
                    "pergunta": q["pergunta"],
                    "sua_resposta": resposta,
                    "resposta_correta": correta_full,
                    "justificativa": q.get("justificativa", "N/A"),
                    "disciplina": disciplina_nome if disciplina_nome else "Avulso"
                }
                if error_entry not in st.session_state.error_log:
                    st.session_state.error_log.append(error_entry)
        st.divider()

    if is_temp:
        if st.button("Voltar para Home"): st.session_state.quiz_to_take = None; st.rerun()
    elif "selected_quiz_id" in st.session_state and st.session_state.selected_quiz_id:
         if st.button("Voltar para lista de quizzes"): st.session_state.selected_quiz_id = None; st.rerun()


def render_flashcard_viewer(deck_data):
    """NOVA FUNÇÃO: Renderiza o visualizador de flashcards para um baralho."""
    
    st.subheader(f"Revisando: {deck_data['nome']}")
    st.caption(f"Disciplina: {deck_data['disciplina']}")
    st.divider()

    try:
        if isinstance(deck_data['cards'], str):
            cards_list = json.loads(deck_data['cards'])
        else:
            cards_list = deck_data['cards']
    except json.JSONDecodeError:
        st.error("Formato de cards inválido.")
        return

    if not cards_list:
        st.warning("Este baralho não contém flashcards.")
        return

    total_cards = len(cards_list)

    # Garantir que o índice é válido
    if st.session_state.flashcard_index >= total_cards:
        st.session_state.flashcard_index = 0
        st.session_state.flashcard_flipped = False

    current_card = cards_list[st.session_state.flashcard_index]
    st.caption(f"Card {st.session_state.flashcard_index + 1} de {total_cards}")

    with st.container(border=True):
        container_style = "min-height: 250px; padding: 20px;"
        st.markdown(f'<div style="{container_style}">', unsafe_allow_html=True)

        if not st.session_state.flashcard_flipped:
            st.subheader("FRENTE:")
            st.write(current_card["frente"])
        else:
            st.subheader("VERSO:")
            st.success(current_card["verso"])
        
        st.markdown('</div>', unsafe_allow_html=True)

    st.divider()
    col1, col2, col3 = st.columns([2, 3, 2])
    with col1:
        if st.button("⬅️ Anterior", use_container_width=True):
            st.session_state.flashcard_index = (st.session_state.flashcard_index - 1) % total_cards
            st.session_state.flashcard_flipped = False
            st.rerun()
    with col2:
        if st.button("🔄 Virar Card", use_container_width=True):
            st.session_state.flashcard_flipped = not st.session_state.flashcard_flipped
            st.rerun()
    with col3:
        if st.button("Próximo ➡️", use_container_width=True):
            st.session_state.flashcard_index = (st.session_state.flashcard_index + 1) % total_cards
            st.session_state.flashcard_flipped = False
            st.rerun()

    if st.button("Voltar para lista de baralhos"):
        st.session_state.selected_deck_id = None
        st.session_state.flashcard_index = 0
        st.session_state.flashcard_flipped = False
        st.rerun()

# -------------------------------
# 🏠 Página Home (Geração)
# -------------------------------
def render_home_page():

    # Estado 0: Se o usuário clicou em "Responder Agora" (Quiz Rápido)
    if st.session_state.quiz_to_take:
        st.header("🎯 Responder Quiz (Temporário)")
        render_quiz_taker(st.session_state.quiz_to_take, is_temp=True)
        return

    st.title("🧠 QuizIA")
    st.subheader("Gere um quiz interativo ou flashcards com IA")
    st.markdown("---")

    # Estado 4: Formulário de Salvamento (Unificado)
    if st.session_state.show_save_form:
        tipo = st.session_state.show_save_form # 'quiz' ou 'deck'
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
                    
        if st.button("Cancelar"):
            st.session_state.show_save_form = None
            st.rerun()

    # Estado 3: Revisão do Quiz Gerado
    elif st.session_state.generated_quiz:
        st.header("Questões Geradas com Sucesso!")
        num_questoes = len(st.session_state.generated_quiz)
        st.success(f"✅ Sucesso! {num_questoes} questões foram geradas.")
        st.info("O que você gostaria de fazer agora?")
        st.markdown("---")
        
        col1, col2 = st.columns(2)
        if col1.button("💾 Salvar Quiz", use_container_width=True):
            st.session_state.show_save_form = 'quiz' # Define o tipo
            st.rerun()
        if col2.button("🎯 Responder Agora (Sem Salvar)", use_container_width=True):
            st.session_state.quiz_to_take = st.session_state.generated_quiz
            st.session_state.generated_quiz = None
            st.rerun()
        if st.button("Descartar", use_container_width=True):
            st.session_state.generated_quiz = None; st.rerun()

    # Estado 3: Revisão dos Flashcards Gerados
    elif st.session_state.generated_flashcards:
        st.header("Flashcards Gerados com Sucesso!")
        num_cards = len(st.session_state.generated_flashcards)
        st.success(f"✅ Sucesso! {num_cards} flashcards foram gerados.")
        st.info("Salve este baralho para poder revisá-lo na aba 'Disciplinas'.")
        
        # Mostra uma prévia
        st.write("**Prévia:**")
        for card in st.session_state.generated_flashcards[:3]: # Mostra os 3 primeiros
            st.write(f"**Frente:** {card['frente']}")
            st.write(f"**Verso:** {card['verso']}")
            st.divider()
        
        st.markdown("---")
        
        col1, col2 = st.columns(2)
        if col1.button("💾 Salvar Baralho", use_container_width=True):
            st.session_state.show_save_form = 'deck' # Define o tipo
            st.rerun()
        if col2.button("Descartar", use_container_width=True):
            st.session_state.generated_flashcards = None; st.rerun()

    # Estado 1: Entrada de Conteúdo
    else:
        st.info("Procurando seus quizzes e baralhos salvos? 📚 Acesse a aba **Disciplinas** no menu.")
        st.markdown("---")

        # Método de entrada (Upload ou Texto)
        input_tabs = st.tabs(["⬆️ Upload de Arquivo", "⌨️ Inserir Texto"])
        
        with input_tabs[0]:
            uploaded_file = st.file_uploader("Envie um PDF", type=["pdf"], label_visibility="collapsed")
        
        with input_tabs[1]:
            texto_manual = st.text_area("Cole o conteúdo aqui", height=250, placeholder="Insira seu texto...", label_visibility="collapsed")

        st.markdown("---")
        st.subheader("O que você quer criar com este material?")

        col1, col2 = st.columns(2)
        
        # Botão Gerar Quiz
        if col1.button("🚀 Gerar Quiz", use_container_width=True):
            texto, tipo = (extract_text_from_pdf(uploaded_file), "PDF") if uploaded_file else (texto_manual, "Texto")
            if not texto: st.warning("Por favor, envie um PDF ou insira texto."); st.stop()
            
            with st.spinner("Gerando quiz... (Isso pode levar um tempo)"):
                chunks = chunk_text(texto)
                questoes_final = []
                progress_bar = st.progress(0, text="Processando partes do texto...")
                for i, chunk in enumerate(chunks):
                    st.info(f"🔹 Processando (Quiz) parte {i+1}/{len(chunks)}...")
                    q = gerar_questoes_deepseek(chunk)
                    if q: q_refinado = refinar_questoes_llama(q); questoes_final.extend(q_refinado)
                    progress_bar.progress((i + 1) / len(chunks), text=f"Parte {i+1}/{len(chunks)} processada")
                    time.sleep(1)
                
                if questoes_final:
                    st.session_state.generated_quiz = questoes_final
                    st.rerun()
                else: st.error("Não foi possível gerar nenhuma questão.")

        # Botão Gerar Flashcards
        if col2.button("🗂️ Gerar Flashcards", use_container_width=True):
            texto, tipo = (extract_text_from_pdf(uploaded_file), "PDF") if uploaded_file else (texto_manual, "Texto")
            if not texto: st.warning("Por favor, envie um PDF ou insira texto."); st.stop()
            
            with st.spinner("Gerando flashcards..."):
                chunks = chunk_text(texto)
                cards_final = []
                progress_bar = st.progress(0, text="Processando partes do texto...")
                for i, chunk in enumerate(chunks):
                    st.info(f"🔹 Processando (Cards) parte {i+1}/{len(chunks)}...")
                    cards = gerar_flashcards_ia(chunk)
                    if cards: cards_final.extend(cards)
                    progress_bar.progress((i + 1) / len(chunks), text=f"Parte {i+1}/{len(chunks)} processada")
                    time.sleep(1)
                
                if cards_final:
                    st.session_state.generated_flashcards = cards_final
                    st.rerun()
                else: st.error("Não foi possível gerar nenhum flashcard.")

# -------------------------------
# 📚 Página Disciplinas (Biblioteca)
# -------------------------------
def render_disciplinas_page():
    st.header("📚 Minhas Disciplinas")

    # --- LÓGICA DE NAVEGAÇÃO 1: Mostrar Visualizador de Flashcard
    if st.session_state.selected_deck_id:
        try:
            deck_data = supabase.table("flashcard_decks").select("*").eq("id", st.session_state.selected_deck_id).single().execute()
            if deck_data.data:
                render_flashcard_viewer(deck_data.data)
            else: st.error("Não foi possível carregar o baralho."); st.session_state.selected_deck_id = None
        except Exception as e:
            st.error(f"Erro ao buscar baralho: {e}"); st.session_state.selected_deck_id = None
        return

    # --- LÓGICA DE NAVEGAÇÃO 2: Mostrar Visualizador de Quiz
    if st.session_state.selected_quiz_id:
        try:
            quiz_data = supabase.table("quizzes").select("nome, questoes, disciplina").eq("id", st.session_state.selected_quiz_id).single().execute()
            if quiz_data.data:
                st.subheader(f"Respondendo: {quiz_data.data['nome']}")
                st.caption(f"Disciplina: {quiz_data.data['disciplina']}")
                st.divider()
                render_quiz_taker(quiz_data.data["questoes"], disciplina_nome=quiz_data.data["disciplina"], is_temp=False)
            else: st.error("Não foi possível carregar o quiz."); st.session_state.selected_quiz_id = None
        except Exception as e:
            st.error(f"Erro ao buscar quiz: {e}"); st.session_state.selected_quiz_id = None
        return

    # --- LÓGICA DE NAVEGAÇÃO 3: Mostrar Detalhes da Disciplina (Abas)
    if st.session_state.selected_disciplina:
        if st.button("← Voltar para todas as disciplinas"):
            st.session_state.selected_disciplina = None; st.rerun()
        
        st.header(f"Disciplina: {st.session_state.selected_disciplina}")
        
        # Abas internas
        tab_quiz, tab_flash, tab_erros = st.tabs(["🎓 Quizzes", "🗂️ Flashcards de Estudo", "❌ Revisão de Erros"])

        with tab_quiz:
            st.subheader(f"Quizzes de {st.session_state.selected_disciplina}")
            try:
                quizzes_disc = supabase.table("quizzes").select("id, nome").eq("disciplina", st.session_state.selected_disciplina).execute()
                if not quizzes_disc.data: st.info("Nenhum quiz encontrado para esta disciplina.")
                else:
                    for quiz in quizzes_disc.data:
                        if st.button(quiz["nome"], key=f"quiz_{quiz['id']}", use_container_width=True):
                            st.session_state.selected_quiz_id = quiz["id"]; st.rerun()
            except Exception as e: st.error(f"Erro ao buscar quizzes: {e}")

        with tab_flash:
            st.subheader(f"Baralhos de {st.session_state.selected_disciplina}")
            try:
                decks_disc = supabase.table("flashcard_decks").select("id, nome").eq("disciplina", st.session_state.selected_disciplina).execute()
                if not decks_disc.data: st.info("Nenhum baralho de flashcards encontrado para esta disciplina.")
                else:
                    for deck in decks_disc.data:
                        if st.button(deck["nome"], key=f"deck_{deck['id']}", use_container_width=True):
                            st.session_state.selected_deck_id = deck["id"]; st.rerun()
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

    # --- LÓGICA DE NAVEGAÇÃO 4: Mostrar Cards de Disciplina (Padrão)
    try:
        # Busca ambas as tabelas
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
if "page" not in st.session_state: st.session_state.page = "Home"
if "generated_quiz" not in st.session_state: st.session_state.generated_quiz = None
if "generated_flashcards" not in st.session_state: st.session_state.generated_flashcards = None # NOVO
if "show_save_form" not in st.session_state: st.session_state.show_save_form = None # MUDANÇA: None/quiz/deck
if "quiz_to_take" not in st.session_state: st.session_state.quiz_to_take = None
if "error_log" not in st.session_state: st.session_state.error_log = []
if "selected_quiz_id" not in st.session_state: st.session_state.selected_quiz_id = None
if "selected_deck_id" not in st.session_state: st.session_state.selected_deck_id = None # NOVO
if "selected_disciplina" not in st.session_state: st.session_state.selected_disciplina = None
if "filtro_revisao" not in st.session_state: st.session_state.filtro_revisao = "Todas"
if "flashcard_index" not in st.session_state: st.session_state.flashcard_index = 0 # (Usado pelo viewer)
if "flashcard_flipped" not in st.session_state: st.session_state.flashcard_flipped = False # (Usado pelo viewer)

# -------------------------------
# 🧭 Navegação Sidebar
# -------------------------------
def reset_home_states():
    st.session_state.generated_quiz = None
    st.session_state.generated_flashcards = None
    st.session_state.show_save_form = None
    st.session_state.quiz_to_take = None
    st.session_state.selected_quiz_id = None
    st.session_state.selected_disciplina = None
    st.session_state.selected_deck_id = None
    st.session_state.filtro_revisao = "Todas"
    st.session_state.flashcard_index = 0
    st.session_state.flashcard_flipped = False

with st.sidebar:
    st.title("Menu QuizIA")
    
    if st.button("🏠 Home", use_container_width=True):
        st.session_state.page = "Home"
        reset_home_states()
        st.rerun()

    if st.button("📚 Disciplinas", use_container_width=True):
        st.session_state.page = "Disciplinas"
        reset_home_states()
        st.rerun()

    if st.button("❌ Revisão de erros", use_container_width=True):
        st.session_state.page = "Revisão de erros"
        st.session_state.filtro_revisao = "Todas" # Reseta só o filtro
        st.rerun()

    # Botão "Flashcards" foi REMOVIDO da sidebar

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
