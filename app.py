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

# MODELOS
# Nvidia: Usado APENAS para olhar gráficos (Vision)
MODELO_VISAO = "nvidia/nemotron-nano-12b-v2-vl:free" 
# Llama 3.3 70B: O "Cérebro" principal. Muito mais capaz de gerar volume (Texto)
MODELO_TEXTO = "google/gemini-2.0-flash-exp:free"

SITE_URL = "http://quizia.streamlit.app"
SITE_NAME = "QuizIA App"

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
# 2. FUNÇÕES DE EXTRAÇÃO
# ------------------------------------------------------------
def extract_content_from_pdf(uploaded_file):
    """Extrai texto e imagens de PDF."""
    doc = fitz.open(stream=uploaded_file.read(), filetype="pdf")
    pages = []

    for i, page in enumerate(doc):
        text = page.get_text()
        # Imagem para fallback visual
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
    tamanho_chunk = 3000 # Aumentei um pouco para o Llama 70B aproveitar o contexto
    chunks = [texto_bruto[i:i+tamanho_chunk] for i in range(0, len(texto_bruto), tamanho_chunk)]
    
    paginas_simuladas = []
    for i, chunk in enumerate(chunks):
        paginas_simuladas.append({
            "page": i + 1,
            "text": chunk,
            "images": [] 
        })
    return paginas_simuladas

def limpar_json_ia(content):
    """Limpeza agressiva de JSON."""
    if not content: return []
    content = re.sub(r"```json|```", "", content)
    match = re.search(r"\[.*\]", content, re.DOTALL)
    if not match:
        return []
    try:
        return json.loads(match.group())
    except:
        return []

def questao_pedagogica(q):
    """Filtra questões visuais inúteis."""
    blacklist = ["cor", "cores", "layout", "design", "estilo visual", "formatação", "fonte"]
    texto = q.get("pergunta", "").lower()
    return not any(b in texto for b in blacklist)

# ------------------------------------------------------------
# 3. ENGINE DE GERAÇÃO (A MÁGICA ACONTECE AQUI)
# ------------------------------------------------------------
def gerar_questoes(pagina, dificuldade, estilo, densidade="Alta"):
    """
    Lógica Híbrida Inteligente:
    1. Se tiver muito texto -> Usa Llama 70B (Gera MUITAS questões).
    2. Se tiver pouco texto mas tiver imagem -> Usa Nvidia (Vision).
    """
    
    # Define a "Agressividade" da geração
    if densidade == "Extrema":
        instrucao_qtd = "Gere uma questão para CADA frase informativa. Mínimo 10 questões por bloco."
    elif densidade == "Alta":
        instrucao_qtd = "Gere questões para cobrir todos os conceitos principais e secundários. Mínimo 5 questões."
    else:
        instrucao_qtd = "Gere questões sobre os pontos principais."

    has_text = len(pagina["text"].strip()) > 50
    
    # DECISÃO DE MODELO: Prioriza Llama 70B para texto (é mais inteligente)
    if has_text:
        modelo_uso = MODELO_TEXTO
        conteudo_prompt = f"TEXTO BASE:\n{pagina['text']}"
        input_msg = [{"type": "text", "text": "..."}] # Placeholder
    else:
        # Só usa visão se não tiver texto legível
        modelo_uso = MODELO_VISAO
        conteudo_prompt = "Analise a IMAGEM fornecida (Gráfico/Tabela)."

    prompt = f"""
    MISSÃO: Você é um gerador de provas implacável.
    {instrucao_qtd}
    
    COBERTURA EXAUSTIVA:
    - Não resuma.
    - Se o texto menciona datas, números, definições ou nomes, CRIE UMA QUESTÃO.
    - Varra o texto do início ao fim.
    
    CONFIGURAÇÃO:
    - Dificuldade: {dificuldade}
    - Estilo: {estilo}
    
    {conteudo_prompt}
    
    FORMATO JSON OBRIGATÓRIO (Responda APENAS o JSON):
    [
      {{
        "pergunta": "Enunciado...",
        "opcoes": ["A) ...", "B) ...", "C) ...", "D) ..."],
        "resposta_correta": "A) ... (cópia exata da opção)",
        "trecho_referencia": "Citação curta do texto",
        "pagina": {pagina["page"]},
        "tipo": "multipla_escolha"
      }}
    ]
    """

    messages = [{"type": "text", "text": prompt}]
    
    # Se for usar visão (Nvidia), anexa imagem
    if modelo_uso == MODELO_VISAO and pagina["images"]:
        for img in pagina["images"]:
            messages.append({"type": "image_url", "image_url": {"url": img}})

    try:
        response = client_ai.chat.completions.create(
            model=modelo_uso,
            messages=[{"role": "user", "content": messages}],
            extra_headers=HEADERS,
            temperature=0.3, # Baixa temperatura para focar em fatos
            max_tokens=4000  # Aumentado para permitir respostas longas (muitas questões)
        )
        raw = limpar_json_ia(response.choices[0].message.content)
        return [q for q in raw if questao_pedagogica(q)]
    except Exception as e:
        st.error(f"Erro na geração ({modelo_uso}): {e}")
        return []

# ------------------------------------------------------------
# 4. SUPABASE
# ------------------------------------------------------------
def salvar_quiz_db(disciplina, tema, questoes):
    try:
        data = { "disciplina": disciplina, "nome": tema, "questoes": json.dumps(questoes) }
        supabase.table("quizzes").insert(data).execute()
        return True
    except Exception as e:
        st.error(f"Erro ao salvar: {e}")
        return False

def carregar_quizzes_db():
    try:
        return supabase.table("quizzes").select("*").order("created_at", desc=True).execute().data
    except: return []

# ------------------------------------------------------------
# 5. UI DO QUIZ
# ------------------------------------------------------------
# ------------------------------------------------------------
# 5. UI DO QUIZ (CORRIGIDO)
# ------------------------------------------------------------
def render_quiz_runner():
    # 1. Proteção: Verifica se existem questões carregadas
    if "questoes" not in st.session_state or not st.session_state.questoes:
        st.warning("Nenhuma questão carregada. Volte e gere um quiz.")
        return

    # 2. Inicialização: Garante que o contador existe
    if "questao_atual" not in st.session_state:
        st.session_state.questao_atual = 0
        
    # 3. Inicialização: Garante que o banco de erros existe
    if "banco_erros" not in st.session_state:
        st.session_state.banco_erros = []

    questoes = st.session_state.questoes
    i = st.session_state.questao_atual
    
    # Proteção extra: Se o índice estourar (ex: deletou questões), reseta
    if i >= len(questoes):
        st.session_state.questao_atual = 0
        i = 0
    
    # Barra de Progresso
    st.progress((i + 1) / len(questoes))
    
    q = questoes[i]
    
    st.markdown(f"### Questão {i+1} de {len(questoes)}")
    
    # Exibe fonte/referência de forma elegante
    fonte = q.get('trecho_referencia', '...')
    if len(fonte) > 100: fonte = fonte[:100] + "..."
    st.info(f"📄 Pág {q.get('pagina', '?')} | Fonte: *{fonte}*")
    
    st.markdown(f"#### {q['pergunta']}")

    # Radio button com chave única para não conflitar
    resposta = st.radio(
        "Alternativas:", 
        q["opcoes"], 
        key=f"resp_{i}_{id(q)}", # Key única baseada no ID da questão
        index=None
    )

    if st.button("Verificar Resposta"):
        if not resposta:
            st.warning("Selecione uma opção!")
        else:
            # Lógica de correção (Pega primeira letra)
            letra_user = resposta.strip()[0].upper()
            gabarito_raw = q.get("resposta_correta", "A")
            letra_gabarito = gabarito_raw.strip()[0].upper()
            
            if letra_user == letra_gabarito:
                st.success("✅ Resposta Correta!")
                st.balloons()
            else:
                st.error(f"❌ Incorreto. Correta: {letra_gabarito}")
                st.markdown(f"**Gabarito:** {q['resposta_correta']}")
                
                # Salva no banco de erros se não existir
                erro_existente = any(e['pergunta'] == q['pergunta'] for e in st.session_state.banco_erros)
                if not erro_existente:
                    st.session_state.banco_erros.append({
                        "pergunta": q["pergunta"],
                        "sua": resposta,
                        "correta": q["resposta_correta"],
                        "expl": q.get('trecho_referencia', '')
                    })

    # Botões de Navegação
    c1, c2 = st.columns([1, 1])
    with c1:
        if st.button("⬅️ Anterior") and i > 0:
            st.session_state.questao_atual -= 1
            st.rerun()
    with c2:
        if st.button("➡️ Próxima") and i < len(questoes) - 1:
            st.session_state.questao_atual += 1
            st.rerun()

# ------------------------------------------------------------
# 6. PÁGINAS
# ------------------------------------------------------------
def page_home():
    col1, col2 = st.columns([0.25, 0.85]) 
    
    with col1:
        st.image("https://media.tenor.com/drzSGxNJG3sAAAAi/cbse-tayari.gif", width=80)
    
    with col2:
        # margin-top: 0 remove o espaço em branco acima do título
        # vertical-align ajuda a centralizar com a imagem se precisar
        st.markdown("""
            <h1 style='margin-top: 0; padding-top: 0;'>
                Quiz<span style='color: #9370DB;'>IA</span>
            </h1>
            """, unsafe_allow_html=True)
    
    c1, c2 = st.columns(2)
    disciplina = c1.text_input("Disciplina", placeholder="Ex: Biologia")
    tema = c2.text_input("Tema", placeholder="Ex: Fotossíntese")
    
    st.markdown("---")
    
    # CONTROLE DE DENSIDADE (NOVIDADE)
    st.subheader("Configuração de Geração")
    col_d, col_dif = st.columns(2)
    densidade = col_d.select_slider("Quantidade de Questões (Densidade)", options=["Padrão", "Alta", "Extrema"], value="Extrema")
    dificuldade = col_dif.selectbox("Dificuldade", ["Fácil", "Médio", "Difícil/Técnico"], index=1)

    tab_pdf, tab_texto = st.tabs(["📂 PDF (Completo)", "📝 Texto (Colar)"])
    paginas_processar = []
    
    with tab_pdf:
        pdf = st.file_uploader("Envie seu PDF", type="pdf")
        if pdf:
            paginas_processar = extract_content_from_pdf(pdf)
            st.success(f"{len(paginas_processar)} páginas carregadas.")

    with tab_texto:
        texto = st.text_area("Cole seu texto:", height=150)
        if texto:
            paginas_processar = chunk_text_manual(texto)

    if st.button("🚀 Gerar Quiz Completo (Varredura Total)", type="primary"):
        if not paginas_processar:
            st.warning("Envie um arquivo primeiro.")
            return
            
        all_questoes = []
        bar = st.progress(0)
        status = st.empty()
        
        for idx, p in enumerate(paginas_processar):
            status.text(f"Analisando Página {p['page']}... (Usando IA para extração profunda)")
            # Chama a função com a nova lógica de densidade
            q_batch = gerar_questoes(p, dificuldade, "Técnico", densidade)
            all_questoes.extend(q_batch)
            bar.progress((idx + 1) / len(paginas_processar))
            
        if all_questoes:
            st.session_state.questoes = all_questoes
            st.session_state.disciplina_atual = disciplina
            st.session_state.tema_atual = tema
            st.session_state.page = "quiz"
            st.rerun()
        else:
            st.error("A IA não retornou questões. Tente colar o texto manualmente se o PDF for imagem.")

def page_library():
    st.title("📚 Biblioteca")
    quizzes = carregar_quizzes_db()
    if not quizzes: st.info("Vazio."); return

    for q in quizzes:
        with st.expander(f"📂 {q['disciplina']} - {q['nome']} ({q['created_at'][:10]})"):
            if st.button(f"Carregar", key=f"load_{q['id']}"):
                st.session_state.questoes = json.loads(q['questoes'])
                st.session_state.questao_atual = 0
                st.session_state.banco_erros = []
                st.session_state.page = "quiz"
                st.rerun()

def page_quiz():
    st.title(f"Quiz: {st.session_state.get('tema_atual', 'Geral')}")
    if st.button("💾 Salvar na Biblioteca"):
        if salvar_quiz_db(st.session_state.get('disciplina_atual', 'Geral'), st.session_state.get('tema_atual', 'Sem Título'), st.session_state.questoes):
            st.toast("Salvo!", icon="✅")
    st.markdown("---")
    render_quiz_runner()

def page_erros():
    st.title("❌ Erros")
    if not st.session_state.get("banco_erros"): st.success("Sem erros."); return
    for e in st.session_state.banco_erros:
        st.error(f"{e['pergunta']}")
        st.write(f"❌ Sua: {e['sua']} | ✅ Correta: {e['correta']}")
        st.caption(f"Explicação: {e['expl']}")
        st.markdown("---")

# ------------------------------------------------------------
# 7. NAVEGAÇÃO
# ------------------------------------------------------------
st.set_page_config("QuizIA Pro", layout="centered")

if "page" not in st.session_state: st.session_state.page = "home"

with st.sidebar:
    st.title("QuizIA Pro")
    if st.button("🏠 Novo Quiz"): st.session_state.page = "home"; st.rerun()
    if st.button("📚 Biblioteca"): st.session_state.page = "library"; st.rerun()
    if st.button("📝 Responder"): st.session_state.page = "quiz"; st.rerun()
    if st.button("❌ Erros"): st.session_state.page = "erros"; st.rerun()

if st.session_state.page == "home": page_home()
elif st.session_state.page == "library": page_library()
elif st.session_state.page == "quiz": page_quiz()
elif st.session_state.page == "erros": page_erros()
