import streamlit as st
import fitz # PyMuPDF
import json
from supabase import create_client, Client
from openai import OpenAI
import time
import uuid
import re # Importando regex para limpeza de JSON

# -------------------------------
# 🔑 Configurações
# -------------------------------
SUPABASE_URL = st.secrets.get("SUPABASE_URL", "SUA_URL_AQUI")
SUPABASE_KEY = st.secrets.get("SUPABASE_KEY", "SUA_CHAVE_AQUI")
OPENROUTER_API_KEY = st.secrets.get("OPENROUTER_API_KEY", "SUA_CHAVE_AQUI")

# Configurações Adicionais do OpenRouter
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
    
    # Mapeamento do estilo escolhido no Streamlit para a instrução do Prompt
    if "Múltipla Escolha" in estilo:
        estilo_prompt = "Múltipla escolha (4 alternativas A-D)"
    elif "Verdadeiro/Falso" in estilo:
        estilo_prompt = "Verdadeiro ou Falso (resposta deve ser 'V' ou 'F')"
    elif "Resposta Curta" in estilo:
        estilo_prompt = "Resposta aberta (pergunta cuja resposta correta seja textual)"
    elif "Preencher Lacuna" in estilo:
        estilo_prompt = "Preencher lacuna (fornecer texto com uma lacuna “_____”)"
    elif "Estilo Misto" in estilo:
        estilo_prompt = "Misturar os 4 tipos de questão: Múltipla escolha, Verdadeiro ou Falso, Resposta aberta e Preencher lacuna."
    else:
        estilo_prompt = "Múltipla escolha (4 alternativas A-D)" # Padrão
    
    prompt = f"""
Você deve gerar questões SOMENTE com base no conteúdo abaixo.
NÃO invente nada que não esteja explícito no texto.

CONTEÚDO BASE:
{texto}

TIPO DE QUESTÃO SOLICITADO: {estilo_prompt}
NÍVEL: {dificuldade}

INSTRUÇÕES DE GERAÇÃO:
1. Múltipla escolha → 4 alternativas (A–D)
2. Verdadeiro ou Falso → resposta deve ser "V" ou "F"
3. Resposta aberta → gere uma pergunta cuja resposta correta seja textual
4. Preencher lacuna → forneça texto com uma lacuna “_____”

FORMATO DE RESPOSTA OBRIGATÓRIO (JSON):
[
  {{
    "tipo": "multipla_escolha" | "vf" | "aberta" | "lacuna",
    "pergunta": "...",
    "opcoes": ["A) ...", "B) ...", "C) ...", "D) ..."], # usar apenas se tipo for multipla_escolha
    "resposta_correta": "...",
    "trecho_referencia": "copie aqui o trecho EXATO do PDF que embasa a resposta"
  }}
]
"""

    try:
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

    client = create_openrouter_client()

    prompt = f"""
Compare a resposta do aluno com a resposta correta.
Avalie a proximidade semântica numa escala de 0 a 100.

RESPOSTA DO ALUNO:
{resposta_usuario}

RESPOSTA CORRETA:
{resposta_correta}

TRECHO DE REFERÊNCIA:
{trecho_referencia}

INSTRUÇÕES PARA EXPLICAÇÃO DIDÁTICA:
Crie uma explicação extremamente didática e útil, com analogias e/ou sugestões de palácio da memória, e use o trecho EXATO do PDF/material fornecido para embasamento.

Retorne em JSON:
{{
  "similaridade": 0-100,
  "correto": true/false,
  "explicacao": "Explicação extremamente didática, com analogias e palácio da memória, usando o trecho do PDF."
}}
"""
    try:
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
# 💾 Funções do Supabase (Removendo Flashcard)
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

# -------------------------------
# 🎯 Funções de Renderização de UI (Ajustadas)
# -------------------------------

# Função auxiliar para renderizar a checagem da questão (manter a lógica)
# [Parte do código omitida para brevidade, pois a lógica de checagem do quiz não muda]

# -------------------------------
# 🏠 Página Home (Geração)
# -------------------------------
# -------------------------------
# 🎯 Funções de Renderização de UI (Continuação)
# -------------------------------

def render_quiz_taker(questoes_json, disciplina_nome="Geral", is_temp=False):
    
    # Decodifica o JSON se não for um objeto Python
    try:
        if isinstance(questoes_json, str):
            questoes = json.loads(questoes_json)
        else:
            questoes = questoes_json
    except json.JSONDecodeError:
        st.error("Erro ao decodificar as questões do quiz.")
        return

    st.subheader(f"Quiz com {len(questoes)} Questões")
    
    if is_temp:
        st.caption("Este é um quiz temporário gerado a partir da página Home. Não será salvo o progresso.")
    
    # Inicializa o estado para armazenar as respostas do usuário e o status de checagem
    if f"respostas_{id(questoes)}" not in st.session_state:
        st.session_state[f"respostas_{id(questoes)}"] = {}
    
    if f"verificado_{id(questoes)}" not in st.session_state:
        st.session_state[f"verificado_{id(questoes)}"] = False

    # Botão para voltar (se não for temporário)
    if not is_temp and st.button("← Voltar para a Disciplina"):
        st.session_state.selected_quiz_id = None
        st.rerun()

    st.markdown("---")

    # Inicia o formulário para submeter todas as respostas de uma vez
    with st.form(key=f"quiz_form_{id(questoes)}"):
        
        for i, q in enumerate(questoes):
            
            pergunta = q.get("pergunta", "Questão sem texto.")
            tipo = q.get("tipo", "multipla_escolha")
            
            st.markdown(f"**Questão {i+1}:**")
            st.write(pergunta)
            
            # --- Renderização do Widget de Resposta ---
            resposta_key = f"resp_{id(questoes)}_{i}"
            
            if tipo == "multipla_escolha":
                opcoes = q.get("opcoes", ["A) Opção A", "B) Opção B"])
                # Usamos um radio button com um valor placeholder inicial
                st.session_state[resposta_key] = st.radio(
                    "Selecione uma opção:",
                    opcoes,
                    index=None,
                    key=resposta_key,
                    label_visibility="collapsed"
                )
            
            elif tipo == "vf":
                opcoes_vf = ["V - Verdadeiro", "F - Falso"]
                st.session_state[resposta_key] = st.radio(
                    "Escolha:",
                    opcoes_vf,
                    index=None,
                    key=resposta_key,
                    label_visibility="collapsed"
                )
            
            elif tipo == "aberta" or tipo == "lacuna":
                # Usamos um text_area para permitir respostas mais longas
                st.session_state[resposta_key] = st.text_area(
                    "Sua resposta:",
                    key=resposta_key,
                    height=80,
                    label_visibility="collapsed"
                )
            
            st.session_state[f"respostas_{id(questoes)}"][i] = st.session_state[resposta_key]

            # Se o quiz já foi verificado, mostramos o feedback em tempo real
            if st.session_state[f"verificado_{id(questoes)}"]:
                # Aqui você chama a lógica de checagem
                resposta_usuario = st.session_state[f"respostas_{id(questoes)}"][i]
                
                # O ideal seria chamar a lógica de checagem que você já tem no código
                # Vamos simplificar mostrando apenas o feedback final para este exercício
                
                correta = q.get("resposta_correta", "Não definida")
                if tipo == "multipla_escolha":
                    if resposta_usuario and resposta_usuario.startswith(correta):
                        st.success("✅ Correto")
                    else:
                        st.error(f"❌ Incorreto. Correta: {correta}")
                elif tipo == "vf":
                    if resposta_usuario and resposta_usuario[0].upper() == correta.upper():
                        st.success("✅ Correto")
                    else:
                        st.error(f"❌ Incorreto. Correta: {correta}")
                elif tipo == "aberta":
                    # Para questões abertas, o feedback deve vir da avaliação da IA
                    st.info("⚠️ Para feedback completo em questões abertas, o código final precisa chamar `avaliar_resposta_aberta`.")
                    st.success(f"Resposta Correta: {correta}")


            st.markdown("---")
        
        # --- Botão de Submissão ---
        submitted = st.form_submit_button("Finalizar e Verificar Respostas", type="primary")

        if submitted:
            # MARCAR COMO VERIFICADO E REDESENHAR A PÁGINA
            st.session_state[f"verificado_{id(questoes)}"] = True
            
            # --- LOGICA PARA CALCULAR PONTUAÇÃO (Opcional) ---
            # Aqui você faria um loop final para chamar o render_quiz_check para cada questão
            # e calcularia a pontuação.

            st.toast("Respostas verificadas! Veja o feedback abaixo de cada questão.")
            st.rerun() # Redesenha a página para exibir o feedback
            
    # Botão para voltar (se não for temporário)
    if not is_temp and st.button("Voltar (Sair do Quiz)", key="voltar_final"):
        st.session_state.selected_quiz_id = None
        st.rerun()

# -------------------------------
# 📚 Página Disciplinas (Biblioteca) - Removendo Flashcards
# -------------------------------
def render_disciplinas_page():
    st.header("📚 Minhas Disciplinas")

    # --- Caixa de Confirmação de Exclusão (Ajustada) ---
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
        return
    # --- Fim da mudança ---

    # Lógica de Navegação 2: Mostrar Visualizador de Quiz
    if st.session_state.selected_quiz_id:
        try:
            quiz_data = supabase.table("quizzes").select("nome, questoes, disciplina").eq("id", st.session_state.selected_quiz_id).single().execute()
            if quiz_data.data:
                st.subheader(f"Respondendo: {quiz_data.data['nome']}")
                st.caption(f"Disciplina: {quiz_data.data['disciplina']}")
                st.divider()
                # Função render_quiz_taker não fornecida
                st.warning("Função render_quiz_taker não implementada. Não é possível iniciar o quiz.")
            else: st.error("Não foi possível carregar o quiz."); st.session_state.selected_quiz_id = None
        except Exception as e: st.error(f"Erro ao buscar quiz: {e}"); st.session_state.selected_quiz_id = None
        return

    # Lógica de Navegação 3: Mostrar Detalhes da Disciplina (Abas)
    if st.session_state.selected_disciplina:
        if st.button("← Voltar para todas as disciplinas"):
            st.session_state.selected_disciplina = None; st.rerun()
        st.header(f"Disciplina: {st.session_state.selected_disciplina}")
        
        tab_quiz, tab_erros = st.tabs(["🎓 Quizzes", "❌ Revisão de Erros"]) # Removido tab_flash

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

        # Tab Flashcard removida

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

    # Lógica de Navegação 4: Mostrar Cards de Disciplina (Padrão)
    try:
        quizzes = supabase.table("quizzes").select("disciplina").execute()
        disciplinas = set()
        if quizzes.data: disciplinas.update([q["disciplina"] for q in quizzes.data])

        if not disciplinas:
            st.warning("Nenhuma disciplina encontrada. Crie um quiz na página Home!"); return

        for disc_nome in sorted(list(disciplinas)):
            with st.container(border=True):
                st.subheader(disc_nome)
                if st.button("Abrir Disciplina", key=f"open_{disc_nome}"):
                    st.session_state.selected_disciplina = disc_nome
                    st.rerun()
            st.markdown("---")
    except Exception as e: st.error(f"Erro ao buscar disciplinas: {e}")

# -------------------------------
# ❌ Página Revisão de Erros (Sem Alteração)
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
# ⚙️ Página Configurar (AJUSTADA com todas as opções de estilo)
# -------------------------------
def render_configurar_page():
    st.header("⚙️ Configurar Geração de IA")
    st.write("Ajuste as preferências para a geração de novos quizzes.")
    st.divider()

    st.subheader("Configurações de Geração")
    
    # Lista completa de estilos de questão
    estilos_disponiveis = [
        "Múltipla Escolha (Padrão)", 
        "Verdadeiro/Falso", 
        "Resposta Curta (beta)",
        "Preencher Lacuna",
        "Estilo Misto (Todos os tipos)"
    ]
    
    # Dificuldade (mantido)
    st.session_state.config_dificuldade = st.radio(
        "Nível de Dificuldade:",
        ["Padrão (Recomendado)", "Fácil (Foco em Conceitos)", "Difícil (Análise Crítica)"],
        key="config_dificuldade_widget",
        horizontal=True,
        index=["Padrão (Recomendado)", "Fácil (Foco em Conceitos)", "Difícil (Análise Crítica)"].index(st.session_state.config_dificuldade)
    )

    # Estilo de Questão (COMPLETO)
    initial_index = estilos_disponiveis.index(st.session_state.config_estilo) if st.session_state.config_estilo in estilos_disponiveis else 0
    
    st.session_state.config_estilo = st.radio(
        "Estilo de Questão (para Quizzes):",
        estilos_disponiveis,
        key="config_estilo_widget",
        horizontal=False, 
        index=initial_index
    )
    
    st.divider()
    st.info("Suas preferências são salvas automaticamente nesta sessão e usadas na página 'Home'.")

# -------------------------------
# 🧩 Interface Principal Streamlit
# -------------------------------
st.set_page_config(page_title="QuizIA", layout="wide")

# -------------------------------
# 💾 Inicialização do st.session_state (Removendo Flashcard)
# -------------------------------
if "page" not in st.session_state: st.session_state.page = "Home"
if "generated_quiz" not in st.session_state: st.session_state.generated_quiz = None
if "show_save_form" not in st.session_state: st.session_state.show_save_form = None
if "quiz_to_take" not in st.session_state: st.session_state.quiz_to_take = None
if "error_log" not in st.session_state: st.session_state.error_log = []
if "selected_quiz_id" not in st.session_state: st.session_state.selected_quiz_id = None
if "selected_disciplina" not in st.session_state: st.session_state.selected_disciplina = None
if "filtro_revisao" not in st.session_state: st.session_state.filtro_revisao = "Todas"

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
# 🧭 Navegação Sidebar (Removendo Flashcard States)
# -------------------------------
def reset_page_states():
    st.session_state.selected_quiz_id = None
    st.session_state.selected_disciplina = None
    st.session_state.filtro_revisao = "Todas"
    st.session_state.generated_quiz = None
    st.session_state.show_save_form = None
    st.session_state.quiz_to_take = None
    st.session_state.confirm_delete_id = None
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
        st.session_state.filtro_revisao = "Todas" 
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
