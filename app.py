# ============================================================
# QUIZIA PRO - VERSÃO CORRIGIDA E COMPLETA
# ============================================================

import streamlit as st
import base64
import fitz  # PyMuPDF
import json
import time
import re
from supabase import create_client, Client
from openai import OpenAI

# ------------------------------------------------------------
# 1. CONFIGURAÇÕES E CHAVES
# ------------------------------------------------------------
try:
    SUPABASE_URL = st.secrets["SUPABASE_URL"]
    SUPABASE_KEY = st.secrets["SUPABASE_KEY"]
    OPENROUTER_API_KEY = st.secrets["OPENROUTER_API_KEY"]
except Exception:
    st.error("❌ Configure as chaves no .streamlit/secrets.toml")
    st.stop()

# Modelos
MODELO_VISAO = "nvidia/nemotron-nano-12b-v2-vl:free"
MODELO_TEXTO = "meta-llama/llama-3.3-70b-instruct:free"

# Configuração do Site
SITE_URL = "http://quizia.streamlit.app"
SITE_NAME = "QuizIA App"

# Clientes
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def create_openrouter_client():
    return OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=OPENROUTER_API_KEY
    )

client_ai = create_openrouter_client()

HEADERS = {
    "HTTP-Referer": SITE_URL,
    "X-Title": SITE_NAME
}

# ------------------------------------------------------------
# 2. FUNÇÕES DE EXTRAÇÃO E PROCESSAMENTO
# ------------------------------------------------------------
def extract_content_from_pdf(uploaded_file):
    """Extrai texto e imagens de PDF."""
    doc = fitz.open(stream=uploaded_file.read(), filetype="pdf")
    pages = []

    for i, page in enumerate(doc):
        text = page.get_text()
        # Renderiza imagem da página para a IA analisar gráficos
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
        img_b64 = base64.b64encode(pix.tobytes("png")).decode()

        pages.append({
            "page": i + 1,
            "text": text,
            "images": [f"data:image/png;base64,{img_b64}"]
        })
    return pages

def chunk_text_manual(texto_bruto):
    """Simula páginas para texto colado manualmente."""
    # Quebra o texto a cada 2000 caracteres para não estourar o prompt
    tamanho_chunk = 2000
    chunks = [texto_bruto[i:i+tamanho_chunk] for i in range(0, len(texto_bruto), tamanho_chunk)]
    
    paginas_simuladas = []
    for i, chunk in enumerate(chunks):
        paginas_simuladas.append({
            "page": i + 1,
            "text": chunk,
            "images": [] # Texto colado não tem imagem
        })
    return paginas_simuladas

def limpar_json_ia(content):
    """Limpa a resposta da IA para garantir JSON válido."""
    content = re.sub(r"```json|```", "", content)
    match = re.search(r"\[.*\]", content, re.DOTALL)
    if not match:
        return []
    try:
        return json.loads(match.group())
    except:
        return []

def questao_pedagogica(q):
    """Filtra questões irrelevantes sobre design."""
    blacklist = ["cor", "cores", "layout", "design", "estilo visual", "formatação", "fonte"]
    texto = q.get("pergunta", "").lower()
    return not any(b in texto for b in blacklist)

# ------------------------------------------------------------
# 3. GERAÇÃO DE QUESTÕES (IA)
# ------------------------------------------------------------
def gerar_questoes(pagina, dificuldade, estilo):
    # Seleciona o modelo: Se tiver imagem, usa Nvidia. Se for só texto, usa Llama (melhor raciocínio).
    modelo_uso = MODELO_VISAO if pagina["images"] else MODELO_TEXTO
    
    prompt = f"""
    MISSÃO:
    Gere questões de prova técnica baseadas APENAS no conteúdo fornecido.
    
    CONFIGURAÇÃO:
    - Dificuldade: {dificuldade}
    - Estilo: {estilo}
    
    CONTEÚDO:
    {pagina["text"]}
    
    FORMATO JSON OBRIGATÓRIO:
    [
      {{
        "pergunta": "Enunciado claro...",
        "opcoes": ["A) ...", "B) ...", "C) ...", "D) ..."],
        "resposta_correta": "A) ... (deve ser idêntica a uma das opções)",
        "trecho_referencia": "Explicação ou trecho do texto que justifica",
        "pagina": {pagina["page"]},
        "tipo": "multipla_escolha"
      }}
    ]
    """

    messages = [{"type": "text", "text": prompt}]
    
    # Adiciona imagens se existirem (apenas para o modelo de visão)
    if pagina["images"] and modelo_uso == MODELO_VISAO:
        for img in pagina["images"]:
            messages.append({"type": "image_url", "image_url": {"url": img}})

    try:
        response = client_ai.chat.completions.create(
            model=modelo_uso,
            messages=[{"role": "user", "content": messages}],
            extra_headers=HEADERS
        )
        raw = limpar_json_ia(response.choices[0].message.content)
        return [q for q in raw if questao_pedagogica(q)]
    except Exception as e:
        st.error(f"Erro na IA: {e}")
        return []

# ------------------------------------------------------------
# 4. SUPABASE (SALVAR E CARREGAR)
# ------------------------------------------------------------
def salvar_quiz_db(disciplina, tema, questoes):
    try:
        data = {
            "disciplina": disciplina,
            "nome": tema, # Usando 'nome' como tema para compatibilidade
            "questoes": json.dumps(questoes)
        }
        supabase.table("quizzes").insert(data).execute()
        return True
    except Exception as e:
        st.error(f"Erro ao salvar: {e}")
        return False

def carregar_quizzes_db():
    try:
        response = supabase.table("quizzes").select("*").order("created_at", desc=True).execute()
        return response.data
    except Exception as e:
        st.error(f"Erro ao carregar: {e}")
        return []

# ------------------------------------------------------------
# 5. INTERFACE DO QUIZ (CORRIGIDA)
# ------------------------------------------------------------
def render_quiz_runner():
    questoes = st.session_state.questoes
    
    if "questao_atual" not in st.session_state:
        st.session_state.questao_atual = 0
    if "banco_erros" not in st.session_state:
        st.session_state.banco_erros = []

    i = st.session_state.questao_atual
    
    # Barra de Progresso
    st.progress((i + 1) / len(questoes))
    
    q = questoes[i]
    
    st.markdown(f"### Questão {i+1} de {len(questoes)}")
    st.info(f"📄 Fonte: Página {q.get('pagina', '?')}")
    st.markdown(f"#### {q['pergunta']}")

    # Radio button
    resposta = st.radio(
        "Selecione a alternativa:",
        q["opcoes"],
        key=f"resp_{i}",
        index=None
    )

    # Botão de Verificar (Para dar feedback imediato)
    if st.button("Verificar Resposta"):
        if not resposta:
            st.warning("Selecione uma opção!")
        else:
            # --- LÓGICA DE CORREÇÃO ROBUSTA ---
            # Pega a primeira letra da resposta do usuário (ex: "A" de "A) Azul")
            letra_user = resposta.strip()[0].upper()
            
            # Pega a primeira letra do gabarito da IA
            gabarito_raw = q["resposta_correta"]
            letra_gabarito = gabarito_raw.strip()[0].upper()
            
            if letra_user == letra_gabarito:
                st.success("✅ Resposta Correta!")
                st.balloons()
            else:
                st.error(f"❌ Incorreto. Você marcou {letra_user}, a correta é {letra_gabarito}.")
                st.markdown(f"**Gabarito:** {q['resposta_correta']}")
                st.info(f"💡 **Explicação:** {q.get('trecho_referencia', 'Sem referência')}")
                
                # Adiciona ao banco de erros se não estiver lá
                erro_existente = any(e['pergunta'] == q['pergunta'] for e in st.session_state.banco_erros)
                if not erro_existente:
                    st.session_state.banco_erros.append({
                        "pergunta": q["pergunta"],
                        "sua": resposta,
                        "correta": q["resposta_correta"],
                        "expl": q.get('trecho_referencia', '')
                    })

    # Navegação
    col1, col2 = st.columns([1, 1])
    with col1:
        if st.button("⬅️ Anterior") and i > 0:
            st.session_state.questao_atual -= 1
            st.rerun()
    with col2:
        if st.button("➡️ Próxima") and i < len(questoes) - 1:
            st.session_state.questao_atual += 1
            st.rerun()

# ------------------------------------------------------------
# 6. PÁGINAS DO APP
# ------------------------------------------------------------
def page_home():
    st.title("🧠 Criar Novo Quiz")
    
    # Configuração de Metadados
    c1, c2 = st.columns(2)
    disciplina = c1.text_input("Disciplina (ex: Redes)")
    tema = c2.text_input("Tema (ex: Camada OSI)")
    
    st.markdown("---")
    
    # Abas para PDF ou Texto
    tab_pdf, tab_texto = st.tabs(["📂 Upload PDF", "📝 Colar Texto"])
    
    paginas_processar = []
    
    with tab_pdf:
        pdf = st.file_uploader("Envie seu arquivo PDF", type="pdf")
        if pdf:
            paginas_processar = extract_content_from_pdf(pdf)
            st.success(f"{len(paginas_processar)} páginas carregadas.")

    with tab_texto:
        texto = st.text_area("Cole seu conteúdo aqui:", height=200)
        if texto:
            paginas_processar = chunk_text_manual(texto)
            st.success(f"Texto dividido em {len(paginas_processar)} partes para análise.")

    # Botão de Geração
    if st.button("🚀 Gerar Quiz com IA", type="primary"):
        if not paginas_processar:
            st.warning("Por favor, forneça um PDF ou Texto.")
            return
            
        if not disciplina or not tema:
            st.warning("Preencha Disciplina e Tema para poder salvar depois.")
            
        all_questoes = []
        bar = st.progress(0)
        
        for idx, p in enumerate(paginas_processar):
            q_batch = gerar_questoes(p, "Intermediário", "Técnico")
            all_questoes.extend(q_batch)
            bar.progress((idx + 1) / len(paginas_processar))
            
        if all_questoes:
            st.session_state.questoes = all_questoes
            st.session_state.disciplina_atual = disciplina
            st.session_state.tema_atual = tema
            st.session_state.page = "quiz"
            st.rerun()
        else:
            st.error("A IA não conseguiu gerar questões deste conteúdo.")

def page_library():
    st.title("📚 Minha Biblioteca")
    quizzes = carregar_quizzes_db()
    
    if not quizzes:
        st.info("Nenhum quiz salvo ainda.")
        return

    for q in quizzes:
        with st.expander(f"📂 {q['disciplina']} - {q['nome']}"):
            st.write(f"Criado em: {q['created_at'][:10]}")
            if st.button(f"Carregar Quiz", key=f"load_{q['id']}"):
                st.session_state.questoes = json.loads(q['questoes'])
                st.session_state.questao_atual = 0
                st.session_state.banco_erros = []
                st.session_state.page = "quiz"
                st.rerun()

def page_quiz():
    st.title(f"Quiz: {st.session_state.get('tema_atual', 'Revisão')}")
    
    # Botão de Salvar no Topo
    if st.button("💾 Salvar este Quiz na Biblioteca"):
        disc = st.session_state.get('disciplina_atual', 'Geral')
        tm = st.session_state.get('tema_atual', 'Sem Título')
        if salvar_quiz_db(disc, tm, st.session_state.questoes):
            st.toast("Quiz salvo com sucesso!", icon="✅")
    
    st.markdown("---")
    render_quiz_runner()

def page_erros():
    st.title("❌ Banco de Erros")
    if not st.session_state.get("banco_erros"):
        st.success("Você ainda não errou nenhuma questão nesta sessão!")
        return
        
    for erro in st.session_state.banco_erros:
        st.error(f"Pergunta: {erro['pergunta']}")
        st.write(f"❌ Sua resposta: {erro['sua']}")
        st.write(f"✅ Correta: {erro['correta']}")
        st.caption(f"📖 Explicação: {erro['expl']}")
        st.markdown("---")

# ------------------------------------------------------------
# 7. CONTROLE DE NAVEGAÇÃO
# ------------------------------------------------------------
st.set_page_config("QuizIA Pro", layout="centered")

if "page" not in st.session_state:
    st.session_state.page = "home"

with st.sidebar:
    st.title("QuizIA Pro")
    if st.button("🏠 Novo Quiz"):
        st.session_state.page = "home"
        st.rerun()
    if st.button("📚 Biblioteca"):
        st.session_state.page = "library"
        st.rerun()
    if st.button("📝 Responder Quiz"):
        if "questoes" in st.session_state:
            st.session_state.page = "quiz"
            st.rerun()
        else:
            st.warning("Crie ou carregue um quiz primeiro.")
    if st.button("❌ Ver Erros"):
        st.session_state.page = "erros"
        st.rerun()

# Roteador de Páginas
if st.session_state.page == "home":
    page_home()
elif st.session_state.page == "library":
    page_library()
elif st.session_state.page == "quiz":
    page_quiz()
elif st.session_state.page == "erros":
    page_erros()
