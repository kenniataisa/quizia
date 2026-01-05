import streamlit as st
import base64
import fitz  # PyMuPDF
import json
import time
import re
import random
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

# --- ATUALIZAÇÃO DOS MODELOS ---
# Usado APENAS para imagens/gráficos (Vision)
MODELO_VISAO = "nvidia/nemotron-nano-12b-v2-vl:free" 
# Usado para texto e raciocínio lógico (Substituto do Gemini)
MODELO_TEXTO = "xiaomi/mimo-v2-flash:free"

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
# 2. FUNÇÕES DE EXTRAÇÃO E UTILITÁRIOS
# ------------------------------------------------------------
def extract_content_from_pdf(uploaded_file):
    doc = fitz.open(stream=uploaded_file.read(), filetype="pdf")
    pages = []
    for i, page in enumerate(doc):
        text = page.get_text()
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
        img_b64 = base64.b64encode(pix.tobytes("png")).decode()
        pages.append({
            "page": i + 1,
            "text": text,
            "images": [f"data:image/png;base64,{img_b64}"]
        })
    return pages

def chunk_text_manual(texto_bruto):
    tamanho_chunk = 3000
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
    if not content: return []
    content = re.sub(r"```json|```", "", content)
    match = re.search(r"\[.*\]", content, re.DOTALL)
    if not match: return []
    try:
        return json.loads(match.group())
    except:
        return []

def questao_pedagogica(q):
    blacklist = ["cor", "cores", "layout", "design", "estilo visual", "formatação", "fonte"]
    texto = q.get("pergunta", "").lower()
    return not any(b in texto for b in blacklist)

# ------------------------------------------------------------
# 3. CORREÇÃO DE DISCURSIVAS COM IA
# ------------------------------------------------------------
def corrigir_discursiva_ia(pergunta, resposta_aluno, gabarito_esperado):
    """Usa a IA para avaliar a resposta aberta do aluno."""
    if not resposta_aluno or len(resposta_aluno.strip()) < 2:
        return False, "Resposta muito curta ou vazia."

    prompt = f"""
    Você é um professor universitário corrigindo uma prova.
    
    PERGUNTA: {pergunta}
    GABARITO/CONCEITO ESPERADO: {gabarito_esperado}
    RESPOSTA DO ALUNO: {resposta_aluno}
    
    A resposta do aluno está correta conceitualmente (mesmo que com outras palavras)?
    
    Responda EXATAMENTE neste formato JSON:
    {{
        "correta": true/false,
        "feedback": "Explique brevemente por que está certo ou errado e o que faltou."
    }}
    """
    
    try:
        resp = client_ai.chat.completions.create(
            model=MODELO_TEXTO,
            messages=[{"role": "user", "content": prompt}],
            extra_headers=HEADERS,
            extra_body={"reasoning": {"enabled": True}}, # Habilita raciocínio
            temperature=0.1
        )
        content = resp.choices[0].message.content
        data = json.loads(re.search(r"\{.*\}", content, re.DOTALL).group())
        return data["correta"], data["feedback"]
    except Exception as e:
        return False, f"Erro na correção automática: {str(e)}"

# ------------------------------------------------------------
# 4. ENGINE DE GERAÇÃO
# ------------------------------------------------------------
def gerar_questoes(pagina, dificuldade, estilo_selecionado, densidade="Alta"):
    
    # 1. Lógica de Dificuldade Aleatória
    dif_real = dificuldade
    if dificuldade == "Aleatória":
        dif_real = random.choice(["Fácil", "Médio", "Difícil/Técnico"])

    # 2. Lógica de Estilo Aleatório
    estilo_prompt = estilo_selecionado
    if estilo_selecionado == "Aleatório (Misturado)":
        estilo_prompt = "Misture questões de Múltipla Escolha, Verdadeiro/Falso e Discursivas."
    
    # Instrução de Quantidade
    if densidade == "Extrema":
        qtd = "Mínimo 8 questões."
    elif densidade == "Alta":
        qtd = "Mínimo 5 questões."
    else:
        qtd = "Gere 3 questões focais."

    has_text = len(pagina["text"].strip()) > 50
    
    # --- SELEÇÃO DE MODELO ---
    if has_text:
        # Usa Xiaomi Mimo se tiver texto
        modelo_uso = MODELO_TEXTO
        conteudo_prompt = f"TEXTO BASE:\n{pagina['text']}"
    else:
        # Usa Nvidia Nemotron APENAS se for imagem/gráfico
        modelo_uso = MODELO_VISAO
        conteudo_prompt = "Analise a IMAGEM fornecida (Gráfico/Tabela/Esquema)."

    prompt = f"""
    MISSÃO: Gerar questões de prova universitária.
    
    CONFIGURAÇÃO:
    - Dificuldade: {dif_real}
    - Estilo das Questões: {estilo_prompt}
    - Quantidade: {qtd}
    
    REGRAS:
    1. Se for 'Múltipla Escolha': Inclua 4 opções (A,B,C,D).
    2. Se for 'Verdadeiro ou Falso': As opções devem ser "Verdadeiro" e "Falso".
    3. Se for 'Discursiva': O campo 'opcoes' deve ser uma lista vazia []. O campo 'resposta_correta' deve conter a explicação ideal.
    
    {conteudo_prompt}
    
    FORMATO JSON OBRIGATÓRIO (Responda APENAS o JSON puro):
    [
      {{
        "tipo": "multipla_escolha" ou "verdadeiro_falso" ou "discursiva",
        "pergunta": "Enunciado...",
        "opcoes": ["A) ...", "B) ..."] ou ["Verdadeiro", "Falso"] ou [],
        "resposta_correta": "A letra correta ou a resposta discursiva ideal",
        "trecho_referencia": "Pequeno trecho do texto que comprova a resposta",
        "pagina": {pagina["page"]}
      }}
    ]
    """

    messages = [{"type": "text", "text": prompt}]
    
    # Anexa imagem apenas se estiver usando o modelo de visão
    if modelo_uso == MODELO_VISAO and pagina["images"]:
        for img in pagina["images"]:
            messages.append({"type": "image_url", "image_url": {"url": img}})

    try:
        response = client_ai.chat.completions.create(
            model=modelo_uso,
            messages=[{"role": "user", "content": messages}],
            extra_headers=HEADERS,
            extra_body={"reasoning": {"enabled": True}}, # Habilita raciocínio para ambos
            temperature=0.5,
            max_tokens=4000
        )
        raw = limpar_json_ia(response.choices[0].message.content)
        return [q for q in raw if questao_pedagogica(q)]
    except Exception as e:
        st.error(f"Erro na geração ({modelo_uso}): {e}")
        return []

# ------------------------------------------------------------
# 5. SUPABASE DB
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
# 6. UI DO QUIZ
# ------------------------------------------------------------
def render_quiz_runner():
    if "questoes" not in st.session_state or not st.session_state.questoes:
        st.warning("Nenhuma questão carregada.")
        return

    if "questao_atual" not in st.session_state: st.session_state.questao_atual = 0
    if "banco_erros" not in st.session_state: st.session_state.banco_erros = []
    
    if "feedback_atual" not in st.session_state: st.session_state.feedback_atual = None

    questoes = st.session_state.questoes
    i = st.session_state.questao_atual
    q = questoes[i]
    
    st.progress((i + 1) / len(questoes))
    st.caption(f"Questão {i+1} de {len(questoes)} | Tipo: {q.get('tipo', 'Geral').title()}")

    st.markdown(f"#### {q['pergunta']}")

    user_input = None
    submit = False
    
    # Caso 1: Discursiva
    if q.get("tipo") == "discursiva" or not q.get("opcoes"):
        user_input = st.text_area("Sua resposta:", key=f"txt_{i}", height=100)
        submit = st.button("Corrigir com IA ✨", key=f"btn_{i}")
    
    # Caso 2: Objetiva
    else:
        user_input = st.radio("Selecione:", q["opcoes"], key=f"radio_{i}", index=None)
        submit = st.button("Verificar", key=f"btn_{i}")

    if submit:
        if not user_input:
            st.warning("Responda antes de corrigir!")
        else:
            if q.get("tipo") == "discursiva" or not q.get("opcoes"):
                with st.spinner("A IA está analisando sua resposta..."):
                    is_correct, feedback = corrigir_discursiva_ia(q['pergunta'], user_input, q['resposta_correta'])
                    st.session_state.feedback_atual = {
                        "correta": is_correct,
                        "msg": feedback,
                        "gabarito": q['resposta_correta']
                    }
            else:
                letra_user = str(user_input).strip()[0].upper()
                letra_gabarito = str(q.get("resposta_correta", "A")).strip()[0].upper()
                
                if q.get("tipo") == "verdadeiro_falso":
                    is_correct = (str(user_input).lower() == str(q.get("resposta_correta")).lower())
                else:
                    is_correct = (letra_user == letra_gabarito)

                st.session_state.feedback_atual = {
                    "correta": is_correct,
                    "msg": "Opção correta!" if is_correct else f"A opção correta era: {q['resposta_correta']}",
                    "gabarito": q['resposta_correta']
                }

            if not st.session_state.feedback_atual["correta"]:
                erro_existente = any(e['pergunta'] == q['pergunta'] for e in st.session_state.banco_erros)
                if not erro_existente:
                    st.session_state.banco_erros.append({
                        "pergunta": q["pergunta"],
                        "sua": user_input,
                        "correta": q["resposta_correta"],
                        "expl": q.get('trecho_referencia', '')
                    })

    if st.session_state.feedback_atual:
        fb = st.session_state.feedback_atual
        if fb["correta"]:
            st.success("✅ " + fb.get("msg", "Correto!"))
            if q.get("tipo") == "discursiva":
                st.info(f"**Gabarito Ideal:** {fb['gabarito']}")
        else:
            st.error("❌ Incorreto.")
            st.write(f"**Feedback:** {fb.get('msg')}")
            if q.get("tipo") != "discursiva":
                st.write(f"**Gabarito:** {fb['gabarito']}")

        with st.expander("🔍 Ver Fonte / Referência no PDF"):
            st.markdown(f"**Página:** {q.get('pagina', '?')}")
            st.info(q.get('trecho_referencia', 'Referência não disponível.'))

    st.markdown("---")
    c1, c2 = st.columns([1, 1])
    with c1:
        if st.button("⬅️ Anterior") and i > 0:
            st.session_state.questao_atual -= 1
            st.session_state.feedback_atual = None
            st.rerun()
    with c2:
        if st.button("➡️ Próxima") and i < len(questoes) - 1:
            st.session_state.questao_atual += 1
            st.session_state.feedback_atual = None
            st.rerun()

# ------------------------------------------------------------
# 7. HOME
# ------------------------------------------------------------
def page_home():
    col1, col2 = st.columns([0.25, 0.85]) 
    with col1:
        st.image("https://media.tenor.com/drzSGxNJG3sAAAAi/cbse-tayari.gif", width=80)
    with col2:
        st.markdown("<h1 style='margin-top: 0;'>Quiz<span style='color: #9370DB;'>IA</span> Pro</h1>", unsafe_allow_html=True)
    
    c1, c2 = st.columns(2)
    disciplina = c1.text_input("Disciplina", placeholder="Ex: Anatomia")
    tema = c2.text_input("Tema", placeholder="Ex: Sistema Nervoso")
    
    st.markdown("---")
    st.subheader("⚙️ Configuração do Quiz")
    
    col_dif, col_estilo, col_vol = st.columns(3)
    
    with col_dif:
        dificuldade = st.selectbox(
            "Nível de Dificuldade", 
            ["Aleatória", "Fácil", "Médio", "Difícil/Técnico"], 
            index=0
        )
        
    with col_estilo:
        estilo = st.selectbox(
            "Estilo das Questões", 
            ["Aleatório (Misturado)", "Múltipla Escolha", "Verdadeiro ou Falso", "Discursiva"],
            index=0
        )

    with col_vol:
        densidade = st.select_slider(
            "Volume de Questões", 
            options=["Padrão", "Alta", "Extrema"], 
            value="Alta"
        )

    tab_pdf, tab_texto = st.tabs(["📂 PDF", "📝 Texto"])
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

    if st.button("🚀 Gerar Quiz", type="primary"):
        if not paginas_processar:
            st.warning("Envie um arquivo ou cole um texto primeiro.")
            return
            
        all_questoes = []
        bar = st.progress(0)
        status = st.empty()
        
        for idx, p in enumerate(paginas_processar):
            status.text(f"Processando pág {p['page']}... (Criando questões {dificuldade} / {estilo})")
            q_batch = gerar_questoes(p, dificuldade, estilo, densidade)
            all_questoes.extend(q_batch)
            bar.progress((idx + 1) / len(paginas_processar))
            
        if all_questoes:
            st.session_state.questoes = all_questoes
            st.session_state.disciplina_atual = disciplina
            st.session_state.tema_atual = tema
            st.session_state.page = "quiz"
            st.session_state.feedback_atual = None 
            st.rerun()
        else:
            st.error("Falha ao gerar questões. Tente novamente.")

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
                st.session_state.feedback_atual = None
                st.session_state.page = "quiz"
                st.rerun()

def page_quiz():
    st.title(f"Quiz: {st.session_state.get('tema_atual', 'Geral')}")
    if st.button("💾 Salvar Quiz"):
        if salvar_quiz_db(st.session_state.get('disciplina_atual', 'Geral'), st.session_state.get('tema_atual', 'Sem Título'), st.session_state.questoes):
            st.toast("Salvo!", icon="✅")
    st.markdown("---")
    render_quiz_runner()

def page_erros():
    st.title("❌ Revisão de Erros")
    if not st.session_state.get("banco_erros"): st.success("Nenhum erro registrado."); return
    for e in st.session_state.banco_erros:
        st.error(f"{e['pergunta']}")
        st.write(f"❌ **Sua resposta:** {e['sua']}")
        st.write(f"✅ **Correta:** {e['correta']}")
        with st.expander("Ver Explicação"):
            st.info(e['expl'])
        st.markdown("---")

# ------------------------------------------------------------
# 8. ROTEAMENTO
# ------------------------------------------------------------
st.set_page_config("QuizIA", layout="centered")

if "page" not in st.session_state: st.session_state.page = "home"

with st.sidebar:
    st.title("QuizIA")
    if st.button("🏠 Novo Quiz"): st.session_state.page = "home"; st.rerun()
    if st.button("📚 Biblioteca"): st.session_state.page = "library"; st.rerun()
    if st.button("📝 Responder"): st.session_state.page = "quiz"; st.rerun()
    if st.button("❌ Erros"): st.session_state.page = "erros"; st.rerun()

if st.session_state.page == "home": page_home()
elif st.session_state.page == "library": page_library()
elif st.session_state.page == "quiz": page_quiz()
elif st.session_state.page == "erros": page_erros()
