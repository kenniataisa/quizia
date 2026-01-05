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

# MODELOS
MODELO_VISAO = "nvidia/nemotron-nano-12b-v2-vl:free" 
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
    except: return []

def questao_pedagogica(q):
    blacklist = ["cor", "cores", "layout", "design", "estilo visual", "formatação", "fonte"]
    texto = q.get("pergunta", "").lower()
    return not any(b in texto for b in blacklist)

# ------------------------------------------------------------
# 3. ENGINE DE GERAÇÃO (COM ESTILO E DIFICULDADE)
# ------------------------------------------------------------
def gerar_questoes(pagina, dificuldade, estilo_selecionado, densidade="Alta"):
    
    # 1. Lógica de Dificuldade Aleatória
    dif_final = dificuldade
    if dificuldade == "Aleatória":
        dif_final = random.choice(["Fácil", "Médio", "Difícil/Técnico"])

    # 2. Lógica de Estilo Aleatório
    estilo_final = estilo_selecionado
    if estilo_selecionado == "Aleatório (Misto)":
        estilo_final = random.choice(["Múltipla Escolha", "Dissertativa (Aberta)", "Verdadeiro/Falso"])

    # Instrução de densidade
    if densidade == "Extrema":
        instrucao_qtd = "Gere uma questão para CADA conceito. Mínimo 8 questões."
    elif densidade == "Alta":
        instrucao_qtd = "Gere questões cobrindo conceitos principais. Mínimo 5 questões."
    else:
        instrucao_qtd = "Gere 3 questões sobre os pontos chave."

    has_text = len(pagina["text"].strip()) > 50
    if has_text:
        modelo_uso = MODELO_TEXTO
        conteudo_prompt = f"TEXTO BASE:\n{pagina['text']}"
    else:
        modelo_uso = MODELO_VISAO
        conteudo_prompt = "Analise a IMAGEM fornecida."

    # Prompt ajustado para suportar tipos variados
    prompt = f"""
    MISSÃO: Gerar questões educacionais.
    {instrucao_qtd}
    
    CONFIGURAÇÃO:
    - Dificuldade: {dif_final}
    - Estilo Obrigatório: {estilo_final}
    
    {conteudo_prompt}
    
    INSTRUÇÃO DE TIPO:
    - Se Estilo for "Múltipla Escolha": use "tipo": "multipla_escolha" e preencha "opcoes".
    - Se Estilo for "Verdadeiro/Falso": use "tipo": "verdadeiro_falso" e opcoes ["Verdadeiro", "Falso"].
    - Se Estilo for "Dissertativa (Aberta)": use "tipo": "dissertativa", deixe "opcoes" como lista vazia [], e coloque a resposta ideal em "resposta_correta".

    FORMATO JSON OBRIGATÓRIO:
    [
      {{
        "pergunta": "Enunciado...",
        "opcoes": ["A)...", "B)..."] ou [],
        "resposta_correta": "Gabarito ou texto ideal",
        "trecho_referencia": "Contexto do texto",
        "pagina": {pagina["page"]},
        "tipo": "multipla_escolha" | "dissertativa" | "verdadeiro_falso"
      }}
    ]
    """

    messages = [{"type": "text", "text": prompt}]
    if modelo_uso == MODELO_VISAO and pagina["images"]:
        for img in pagina["images"]:
            messages.append({"type": "image_url", "image_url": {"url": img}})

    try:
        response = client_ai.chat.completions.create(
            model=modelo_uso,
            messages=[{"role": "user", "content": messages}],
            extra_headers=HEADERS,
            temperature=0.5, 
            max_tokens=4000  
        )
        raw = limpar_json_ia(response.choices[0].message.content)
        return [q for q in raw if questao_pedagogica(q)]
    except Exception as e:
        st.error(f"Erro na geração: {e}")
        return []

# ------------------------------------------------------------
# 3.1 FUNÇÃO DE CORREÇÃO COM IA (NOVA)
# ------------------------------------------------------------
def avaliar_resposta_ia(pergunta, resposta_aluno, resposta_ideal, contexto):
    """Usa a IA para corrigir questões abertas."""
    prompt = f"""
    Atue como um professor rigoroso mas didático.
    Avalie a resposta do aluno para a seguinte questão:
    
    PERGUNTA: {pergunta}
    CONTEXTO/FONTE: {contexto}
    GABARITO IDEAL: {resposta_ideal}
    
    RESPOSTA DO ALUNO: {resposta_aluno}
    
    Responda EXATAMENTE neste formato JSON:
    {{
        "correto": true ou false (considere correto se o sentido estiver certo, mesmo com outras palavras),
        "feedback": "Explicação curta de onde acertou ou errou."
    }}
    """
    try:
        response = client_ai.chat.completions.create(
            model=MODELO_TEXTO,
            messages=[{"role": "user", "content": prompt}],
            extra_headers=HEADERS,
            temperature=0.2
        )
        # Tenta limpar markdown caso a IA coloque
        content = response.choices[0].message.content.replace("```json", "").replace("```", "")
        return json.loads(content)
    except:
        return {"correto": False, "feedback": "Erro ao conectar com o corretor IA."}

# ------------------------------------------------------------
# 4. SUPABASE E UI
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
# 5. UI DO QUIZ (ATUALIZADA)
# ------------------------------------------------------------
def render_quiz_runner():
    if "questoes" not in st.session_state or not st.session_state.questoes:
        st.warning("Nenhuma questão carregada.")
        return

    if "questao_atual" not in st.session_state: st.session_state.questao_atual = 0
    if "banco_erros" not in st.session_state: st.session_state.banco_erros = []
    
    # Session state para feedback da IA (para não sumir ao recarregar)
    if "feedback_ia" not in st.session_state: st.session_state.feedback_ia = None

    questoes = st.session_state.questoes
    i = st.session_state.questao_atual
    q = questoes[i]
    
    # Barra de Progresso
    st.progress((i + 1) / len(questoes))
    st.markdown(f"### Questão {i+1}/{len(questoes)}")
    
    # --- REFERÊNCIA OCULTA ---
    with st.expander("🔍 Ver Fonte / Dica (Oculto)"):
        st.info(f"Página {q.get('pagina', '?')}")
        st.write(f"Referência: *{q.get('trecho_referencia', '...')[:300]}*")

    # Enunciado
    st.markdown(f"#### {q['pergunta']}")
    
    tipo = q.get("tipo", "multipla_escolha").lower()
    
    # --- RENDERIZAÇÃO BASEADA NO TIPO ---
    
    if "dissertativa" in tipo:
        # QUESTÃO ABERTA
        resposta_texto = st.text_area("Sua resposta:", key=f"text_{i}_{id(q)}", height=100)
        
        if st.button("🤖 Corrigir com IA"):
            if len(resposta_texto) < 5:
                st.warning("Escreva uma resposta mais completa.")
            else:
                with st.spinner("A IA está lendo sua resposta..."):
                    avaliacao = avaliar_resposta_ia(
                        q['pergunta'], 
                        resposta_texto, 
                        q['resposta_correta'], 
                        q.get('trecho_referencia', '')
                    )
                    st.session_state.feedback_ia = avaliacao
        
        # Exibe Feedback se existir
        if st.session_state.feedback_ia:
            res = st.session_state.feedback_ia
            if res.get("correto"):
                st.success(f"✅ {res.get('feedback')}")
            else:
                st.error(f"❌ {res.get('feedback')}")
                st.markdown(f"**Resposta Ideal:** {q['resposta_correta']}")
                
                # Salvar erro
                if not any(e['pergunta'] == q['pergunta'] for e in st.session_state.banco_erros):
                    st.session_state.banco_erros.append({
                        "pergunta": q["pergunta"],
                        "sua": resposta_texto,
                        "correta": q["resposta_correta"],
                        "expl": res.get("feedback")
                    })

    else:
        # MÚLTIPLA ESCOLHA OU V/F
        opcoes = q.get("opcoes", [])
        if not opcoes: opcoes = ["Verdadeiro", "Falso"] # Fallback
        
        resposta = st.radio("Escolha:", opcoes, key=f"radio_{i}_{id(q)}", index=None)
        
        if st.button("Verificar Resposta"):
            if not resposta:
                st.warning("Selecione uma opção!")
            else:
                # Lógica simples de comparação de string ou primeira letra
                letra_user = resposta.strip()[0].upper()
                letra_gabarito = q.get("resposta_correta", "A").strip()[0].upper()
                
                # Para V/F a comparação deve ser da palavra inteira
                if "verdadeiro" in tipo or "falso" in tipo:
                    acertou = resposta.lower() in q.get("resposta_correta", "").lower()
                else:
                    acertou = letra_user == letra_gabarito

                if acertou:
                    st.success("✅ Resposta Correta!")
                    st.balloons()
                else:
                    st.error("❌ Incorreto.")
                    st.markdown(f"**Gabarito:** {q['resposta_correta']}")
                    if not any(e['pergunta'] == q['pergunta'] for e in st.session_state.banco_erros):
                        st.session_state.banco_erros.append({
                            "pergunta": q["pergunta"],
                            "sua": resposta,
                            "correta": q["resposta_correta"],
                            "expl": q.get('trecho_referencia', '')
                        })

    # Navegação
    st.divider()
    c1, c2 = st.columns([1, 1])
    with c1:
        if st.button("⬅️ Anterior") and i > 0:
            st.session_state.questao_atual -= 1
            st.session_state.feedback_ia = None # Limpa feedback ao mudar
            st.rerun()
    with c2:
        if st.button("➡️ Próxima") and i < len(questoes) - 1:
            st.session_state.questao_atual += 1
            st.session_state.feedback_ia = None # Limpa feedback ao mudar
            st.rerun()

# ------------------------------------------------------------
# 6. HOME
# ------------------------------------------------------------
def page_home():
    st.image("https://media.tenor.com/drzSGxNJG3sAAAAi/cbse-tayari.gif", width=80)
    st.title("QuizIA Pro")
    
    c1, c2 = st.columns(2)
    disciplina = c1.text_input("Disciplina", "Geral")
    tema = c2.text_input("Tema", "Estudos")
    
    st.markdown("---")
    st.subheader("⚙️ Configuração de Geração")
    
    col1, col2 = st.columns(2)
    dificuldade = col1.selectbox("Dificuldade", ["Aleatória", "Fácil", "Médio", "Difícil/Técnico"], index=0)
    estilo = col2.selectbox("Estilo das Questões", ["Aleatório (Misto)", "Múltipla Escolha", "Verdadeiro/Falso", "Dissertativa (Aberta)"], index=0)
    
    densidade = st.select_slider("Quantidade/Densidade", options=["Padrão", "Alta", "Extrema"], value="Alta")

    tab_pdf, tab_texto = st.tabs(["📂 Upload PDF", "📝 Colar Texto"])
    paginas_processar = []
    
    with tab_pdf:
        pdf = st.file_uploader("Arquivo PDF", type="pdf")
        if pdf:
            paginas_processar = extract_content_from_pdf(pdf)
            st.success(f"{len(paginas_processar)} páginas identificadas.")

    with tab_texto:
        texto = st.text_area("Texto Manual", height=150)
        if texto:
            paginas_processar = chunk_text_manual(texto)

    if st.button("🚀 Gerar Quiz", type="primary"):
        if not paginas_processar:
            st.warning("Forneça um PDF ou Texto.")
            return
            
        all_questoes = []
        bar = st.progress(0)
        status = st.empty()
        
        for idx, p in enumerate(paginas_processar):
            status.text(f"Processando parte {idx+1}/{len(paginas_processar)}...")
            # Passa o estilo e dificuldade selecionados
            q_batch = gerar_questoes(p, dificuldade, estilo, densidade)
            all_questoes.extend(q_batch)
            bar.progress((idx + 1) / len(paginas_processar))
            
        if all_questoes:
            st.session_state.questoes = all_questoes
            st.session_state.disciplina_atual = disciplina
            st.session_state.tema_atual = tema
            st.session_state.page = "quiz"
            st.rerun()
        else:
            st.error("Falha ao gerar questões. Tente outro texto.")

def page_library():
    st.title("📚 Biblioteca")
    quizzes = carregar_quizzes_db()
    if not quizzes: st.info("Vazio."); return

    for q in quizzes:
        with st.expander(f"📂 {q['disciplina']} - {q['nome']}"):
            if st.button(f"Carregar", key=f"load_{q['id']}"):
                st.session_state.questoes = json.loads(q['questoes'])
                st.session_state.questao_atual = 0
                st.session_state.banco_erros = []
                st.session_state.page = "quiz"
                st.rerun()

def page_quiz():
    st.title(f"📝 {st.session_state.get('tema_atual', 'Quiz')}")
    if st.button("Salvar Progresso"):
        salvar_quiz_db(st.session_state.get('disciplina_atual'), st.session_state.get('tema_atual'), st.session_state.questoes)
        st.toast("Salvo!")
    st.markdown("---")
    render_quiz_runner()

def page_erros():
    st.title("❌ Revisão de Erros")
    if not st.session_state.get("banco_erros"): st.info("Nenhum erro registrado."); return
    for e in st.session_state.banco_erros:
        st.error(f"P: {e['pergunta']}")
        st.write(f"Sua resposta: {e['sua']}")
        st.success(f"Gabarito: {e['correta']}")
        st.caption(f"Explicação: {e['expl']}")
        st.markdown("---")

# ------------------------------------------------------------
# 7. MAIN
# ------------------------------------------------------------
st.set_page_config("QuizIA Pro", layout="centered")
if "page" not in st.session_state: st.session_state.page = "home"

with st.sidebar:
    st.title("Menu")
    if st.button("🏠 Home"): st.session_state.page = "home"; st.rerun()
    if st.button("📚 Biblioteca"): st.session_state.page = "library"; st.rerun()
    if st.button("📝 Quiz Atual"): st.session_state.page = "quiz"; st.rerun()
    if st.button("❌ Erros"): st.session_state.page = "erros"; st.rerun()

if st.session_state.page == "home": page_home()
elif st.session_state.page == "library": page_library()
elif st.session_state.page == "quiz": page_quiz()
elif st.session_state.page == "erros": page_erros()
