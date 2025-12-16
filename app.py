import streamlit as st
import base64
import fitz  # PyMuPDF
import json
from supabase import create_client, Client
from openai import OpenAI
import time
import re

# -------------------------------
# 🔑 Configurações
# -------------------------------
SUPABASE_URL = st.secrets.get("SUPABASE_URL", "SUA_URL_AQUI")
SUPABASE_KEY = st.secrets.get("SUPABASE_KEY", "SUA_CHAVE_AQUI")
OPENROUTER_API_KEY = st.secrets.get("OPENROUTER_API_KEY", "SUA_CHAVE_AQUI")

MODELO_VISAO = "nvidia/nemotron-nano-12b-v2-vl:free"

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
# 📚 Funções de Extração e Chunk (AJUSTADO PARA GRANULARIDADE)
# -------------------------------
def extract_content_from_pdf(uploaded_file):
    try:
        doc = fitz.open(stream=uploaded_file.read(), filetype="pdf")
        content_pages = []

        for page_num, page in enumerate(doc):
            # 1. Extrair Texto
            text = page.get_text("text")
            
            images_data = []
            image_list = page.get_images(full=True)
            
            for img_index, img in enumerate(image_list):
                xref = img[0]
                base_image = doc.extract_image(xref)
                image_bytes = base_image["image"]
                
                base64_str = base64.b64encode(image_bytes).decode("utf-8")
                images_data.append(f"data:image/png;base64,{base64_str}")

            content_pages.append({
                "page": page_num + 1,
                "text": text,
                "images": images_data # Lista de strings base64
            })
            
        return content_pages
    except Exception as e:
        st.error(f"Erro ao ler PDF: {e}")
        return []

def chunk_text(text, max_chars=2000): # Reduzido para 2000 para forçar mais questões
    """
    Divide o texto em chunks menores.
    Tamanho reduzido para garantir que a IA analise detalhes minuciosos
    e gere mais questões por página.
    """
    paragraphs = text.split("\n")
    chunks = []
    current_chunk = ""
    
    for para in paragraphs:
        para_com_espaco = para + "\n"
        
        # Se adicionar o parágrafo atual ultrapassar o limite
        if len(current_chunk) + len(para_com_espaco) > max_chars:
            
            # 1. Salva o chunk atual se ele não estiver vazio
            if current_chunk:
                chunks.append(current_chunk.strip())
            
            # 2. Se o parágrafo for ENORME, quebra ele
            if len(para_com_espaco) > max_chars:
                for i in range(0, len(para_com_espaco), max_chars):
                    chunks.append(para_com_espaco[i:i + max_chars].strip())
                current_chunk = "" 
            else:
                current_chunk = para_com_espaco
        else:
            current_chunk += para_com_espaco
            
    if current_chunk:
        chunks.append(current_chunk.strip())
        
    return chunks

# -------------------------------
# 🤖 Funções de Geração de IA (PROMPT EXAUSTIVO)
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
        # st.warning(f"Erro ao decodificar JSON. Conteúdo bruto: {content[:100]}...")
        return None

def gerar_questoes_vision_math(pagina_data, dificuldade, estilo):
    """
    Gera questões analisando texto E imagens (gráficos/fórmulas).
    """
    texto = pagina_data['text']
    imagens = pagina_data['images']
    
    # Adicionamos "Cálculo" explicitamente ao prompt
    prompt_text = f"""
    MISSÃO: Analise o texto e as IMAGENS (gráficos, tabelas, fórmulas) desta página.
    Gere um Quiz focando em interpretação visual e CÁLCULOS MATEMÁTICOS se houver dados para isso.
    
    CONTEÚDO DA PÁGINA:
    {texto}
    
    CONFIGURAÇÕES:
    - Nível: {dificuldade}
    - Estilo: {estilo}
    
    INSTRUÇÕES ESPECÍFICAS:
    1. **VISÃO:** Se houver gráficos ou diagramas nas imagens enviadas, crie questões sobre eles (ex: "Com base no gráfico...").
    2. **CÁLCULO:** Se houver fórmulas ou números, crie problemas práticos onde o aluno precise calcular a resposta.
       - Para questões de cálculo, no campo 'trecho_referencia', coloque a resolução passo-a-passo.
    3. FORMATO JSON ESTRITO (igual ao anterior).
    """

    # Montagem da mensagem Multimodal (Texto + Imagens)
    messages_content = [{"type": "text", "text": prompt_text}]
    
    # Adiciona as imagens ao payload
    for img_b64 in imagens:
        messages_content.append({
            "type": "image_url",
            "image_url": {"url": img_b64}
        })

    try:
        response = deepseek_client.chat.completions.create(
            extra_headers=OPENROUTER_HEADERS,
            model=MODELO_VISAO,
            messages=[{"role": "user", "content": messages_content}],
        )
        content = response.choices[0].message.content
        return limpar_json_ia(content, tipo_lista=True) or []
    except Exception as e:
        st.error(f"Erro na API Vision: {e}")
        return []
        
def refinar_questoes_llama(questoes):
    """Refina as questões geradas."""
    if not questoes: return []
    prompt = f"""
    Atue como um professor experiente. Revise as questões abaixo para garantir clareza, correção gramatical e didática.
    Mantenha o formato JSON estritamente idêntico. Não remova questões, apenas melhore o texto.
    
    Questões:
    {json.dumps(questoes, ensure_ascii=False, indent=2)}
    """
    try:
        response = llama_client.chat.completions.create(
            extra_headers=OPENROUTER_HEADERS,
            model="nvidia/nemotron-nano-12b-v2-vl:free", # <--- CORRIGIDO AQUI (era MODELO_VISAO=)
            messages=[{"role": "user", "content": prompt}],
        )
        content = response.choices[0].message.content
        return limpar_json_ia(content, tipo_lista=True) or questoes
    except Exception as e:
        return questoes

def avaliar_resposta_aberta(resposta_usuario, resposta_correta, trecho_referencia):
    """Avaliação inteligente de respostas abertas."""
    client = create_openrouter_client()
    prompt = f"""
    Avalie a resposta do aluno.
    
    ALUNO: {resposta_usuario}
    GABARITO: {resposta_correta}
    FONTE: {trecho_referencia}
    
    Retorne JSON:
    {{
      "similaridade": 0-100,
      "correto": true/false,
      "explicacao": "Explicação didática, usando analogias e palácio da memória se possível, baseada na fonte."
    }}
    """
    try:
        response = client.chat.completions.create(
            extra_headers=OPENROUTER_HEADERS,
            model="nvidia/nemotron-nano-12b-v2-vl:free",
            messages=[{"role": "user", "content": prompt}],
        )
        return limpar_json_ia(response.choices[0].message.content, tipo_lista=False)
    except Exception:
        return {"similaridade": 0, "correto": False, "explicacao": "Erro na avaliação."}

# -------------------------------
# 💾 Funções do Supabase
# -------------------------------
def salvar_quiz(disciplina, nome, questoes):
    try:
        data = { "nome": nome, "disciplina": disciplina, "questoes": json.dumps(questoes) }
        supabase.table("quizzes").insert(data).execute()
        return True
    except Exception as e:
        st.error(f"Erro ao salvar o quiz: {e}"); return False

def deletar_item_supabase(id, tipo):
    tabela = "quizzes"
    try:
        supabase.table(tabela).delete().eq("id", id).execute()
        st.toast(f"{tipo.capitalize()} deletado!", icon="🗑️")
        return True
    except Exception as e:
        st.error(f"Erro ao deletar: {e}"); return False

# -------------------------------
# 🎯 Funções de Renderização de UI
# -------------------------------
def render_quiz_taker(questoes_json, disciplina_nome="Geral", is_temp=False):
    """Interface para responder ao quiz."""
    try:
        questoes = json.loads(questoes_json) if isinstance(questoes_json, str) else questoes_json
    except json.JSONDecodeError:
        st.error("Erro ao carregar questões."); return

    st.subheader(f"📝 Quiz: {len(questoes)} Questões")
    
    quiz_id = id(questoes) # Identificador da sessão do quiz
    
    if f"respostas_{quiz_id}" not in st.session_state:
        st.session_state[f"respostas_{quiz_id}"] = {}
    if f"verificado_{quiz_id}" not in st.session_state:
        st.session_state[f"verificado_{quiz_id}"] = False

    if not is_temp and st.button("← Voltar"):
        st.session_state.selected_quiz_id = None; st.rerun()

    st.markdown("---")

    with st.form(key=f"form_{quiz_id}"):
        respostas_temp = {}
        
        for i, q in enumerate(questoes):
            st.markdown(f"**{i+1}. {q.get('pergunta', '')}**")
            tipo = q.get("tipo", "multipla_escolha")
            r_key = f"q_{quiz_id}_{i}"
            
            if tipo == "multipla_escolha":
                respostas_temp[i] = st.radio("Opções:", q.get("opcoes", []), index=None, key=r_key, label_visibility="collapsed")
            elif tipo == "vf":
                respostas_temp[i] = st.radio("Opções:", ["V", "F"], index=None, key=r_key, label_visibility="collapsed")
            else:
                respostas_temp[i] = st.text_area("Resposta:", key=r_key, label_visibility="collapsed")

            # Feedback Pós-Submissão
            if st.session_state[f"verificado_{quiz_id}"]:
                user_resp = st.session_state[f"respostas_{quiz_id}"].get(i)
                correta = q.get("resposta_correta", "")
                
                if tipo in ["multipla_escolha", "vf", "lacuna"]:
                    # Lógica simples de string matching
                    acertou = False
                    if user_resp and user_resp.lower().strip() in correta.lower().strip(): acertou = True
                    # Ajuste fino para multipla escolha (verifica inicio da string "A)")
                    if tipo == "multipla_escolha" and user_resp and user_resp.split(')')[0] == correta.split(')')[0]: acertou = True
                    
                    if acertou: st.success("✅ Correto!")
                    else: 
                        st.error(f"❌ Errado. Correta: {correta}")
                        st.caption(f"📖 Fonte: {q.get('trecho_referencia')}")
                elif tipo == "aberta":
                    st.info(f"💡 Gabarito sugerido: {correta}")
                    st.warning("Nota: A correção automática detalhada via IA ocorre individualmente (recurso avançado).")

            st.markdown("---")

        if st.form_submit_button("✅ Finalizar e Corrigir"):
            st.session_state[f"respostas_{quiz_id}"] = respostas_temp
            st.session_state[f"verificado_{quiz_id}"] = True
            st.rerun()

def render_home_page():
    if st.session_state.quiz_to_take:
        render_quiz_taker(st.session_state.quiz_to_take, is_temp=True)
        return

    st.title("🧠 QuizIA - Modo Exaustivo")
    st.info("Geração sem limites: O sistema tentará extrair todas as questões possíveis do seu arquivo.")
    
    # Save Form
    if st.session_state.show_save_form:
        with st.form("save"):
            st.subheader("💾 Salvar Quiz Gerado")
            disc = st.text_input("Disciplina")
            nome = st.text_input("Nome do Quiz")
            if st.form_submit_button("Salvar"):
                if salvar_quiz(disc, nome, st.session_state.generated_quiz):
                    st.success("Salvo!"); st.session_state.generated_quiz = None; st.session_state.show_save_form = None; st.rerun()
        if st.button("Cancelar"): st.session_state.show_save_form = None; st.rerun()
        return

    # Results View
    if st.session_state.generated_quiz:
        st.success(f"🎉 **{len(st.session_state.generated_quiz)} questões geradas!**")
        c1, c2, c3 = st.columns(3)
        if c1.button("💾 Salvar"): st.session_state.show_save_form = True; st.rerun()
        if c2.button("📝 Responder Agora"): st.session_state.quiz_to_take = st.session_state.generated_quiz; st.session_state.generated_quiz = None; st.rerun()
        if c3.button("🗑️ Descartar"): st.session_state.generated_quiz = None; st.rerun()
        return

    # Inputs
    tab1, tab2 = st.tabs(["Upload PDF", "Colar Texto"])
    with tab1: f = st.file_uploader("PDF", type="pdf", label_visibility="collapsed")
    with tab2: t = st.text_area("Texto", height=200, label_visibility="collapsed")

    # ... (dentro de render_home_page) ...
    
    # Botão de Ação
    if st.button("🚀 Gerar Quiz Completo", type="primary"):
        paginas_conteudo = []
        
        # 1. Se for PDF
        if f:
            paginas_conteudo = extract_content_from_pdf(f)
            
        # 2. Se for Texto Colado (Fallback)
        elif t:
            # Dividimos o texto em pedaços e criamos "páginas falsas" sem imagens
            chunks = chunk_text(t)
            for i, chunk in enumerate(chunks):
                paginas_conteudo.append({
                    "page": i + 1,
                    "text": chunk,
                    "images": [] # Lista vazia, pois não tem imagem
                })
        
        # Validação
        if not paginas_conteudo:
            st.warning("Por favor, envie um PDF ou cole um texto.")
            st.stop()
        
        # 3. Processamento Unificado
        todas_questoes = []
        bar = st.progress(0)
        status = st.empty()
        
        for i, pagina in enumerate(paginas_conteudo):
            status.text(f"Analisando Parte {pagina['page']} de {len(paginas_conteudo)}...")
            
            # Chama a IA (Se não tiver imagem, ela analisa só o texto)
            q_pagina = gerar_questoes_vision_math(pagina, st.session_state.config_dificuldade, st.session_state.config_estilo)
            
            if q_pagina:
                # Opcional: Refinar com Llama (pode descomentar se quiser)
                # q_pagina = refinar_questoes_llama(q_pagina)
                todas_questoes.extend(q_pagina)
            
            bar.progress((i+1)/len(paginas_conteudo))
            
        if todas_questoes:
            st.session_state.generated_quiz = todas_questoes
            st.rerun()
        else:
            st.error("Não foi possível gerar questões. Tente outro arquivo.")

# -------------------------------
# 📚 Página Disciplinas
# -------------------------------
def render_disciplinas_page():
    st.header("Minha Biblioteca")
    
    # Navegação para Quiz Salvo
    if st.session_state.selected_quiz_id:
        data = supabase.table("quizzes").select("*").eq("id", st.session_state.selected_quiz_id).single().execute()
        if data.data:
            render_quiz_taker(data.data['questoes'], data.data['disciplina'])
        else:
            st.error("Erro ao abrir."); st.session_state.selected_quiz_id = None
        return

    # Lista de Disciplinas
    try:
        resp = supabase.table("quizzes").select("id, nome, disciplina").execute()
        if not resp.data: st.info("Nada salvo ainda."); return
        
        # Agrupar
        from collections import defaultdict
        disc_map = defaultdict(list)
        for item in resp.data: disc_map[item['disciplina']].append(item)
        
        for disc, itens in disc_map.items():
            with st.expander(f"📂 {disc}", expanded=True):
                for q in itens:
                    c1, c2 = st.columns([0.85, 0.15])
                    if c1.button(f"📝 {q['nome']}", key=q['id']):
                        st.session_state.selected_quiz_id = q['id']; st.rerun()
                    if c2.button("❌", key=f"del_{q['id']}"):
                        deletar_item_supabase(q['id'], "quiz"); st.rerun()
    except Exception as e:
        st.error(f"Erro de conexão: {e}")

# -------------------------------
# ⚙️ Configuração
# -------------------------------
def render_configurar_page():
    st.header("Preferências de IA")
    
    st.session_state.config_dificuldade = st.radio(
        "Dificuldade", 
        ["Padrão (Recomendado)", "Fácil (Foco em Conceitos)", "Difícil (Análise Crítica)"],
        index=0
    )
    
    st.session_state.config_estilo = st.selectbox(
        "Estilo das Questões",
        ["Múltipla Escolha (Padrão)", "Verdadeiro/Falso", "Resposta Curta (beta)", "Preencher Lacuna", "Estilo Misto (Todos os tipos)"],
        index=4 # Padrão Misto
    )

# -------------------------------
# 🚦 Main Loop
# -------------------------------
st.set_page_config(page_title="QuizIA Pro", layout="centered")

# Init States
defaults = {
    "page": "Home", "generated_quiz": None, "show_save_form": None,
    "quiz_to_take": None, "selected_quiz_id": None, "error_log": [],
    "config_dificuldade": "Padrão (Recomendado)", 
    "config_estilo": "Estilo Misto (Todos os tipos)",
    "confirm_delete_id": None # Caso precise restaurar a lógica de confirmação
}
for k, v in defaults.items():
    if k not in st.session_state: st.session_state[k] = v

# Sidebar
with st.sidebar:
    st.title("QuizIA")
    if st.button("🏠 Criar Quiz"): st.session_state.page = "Home"; st.session_state.generated_quiz = None; st.rerun()
    if st.button("📚 Meus Quizzes"): st.session_state.page = "Disciplinas"; st.session_state.selected_quiz_id = None; st.rerun()
    if st.button("⚙️ Configurar"): st.session_state.page = "Configurar"; st.rerun()

# Router
if st.session_state.page == "Home": render_home_page()
elif st.session_state.page == "Disciplinas": render_disciplinas_page()
elif st.session_state.page == "Configurar": render_configurar_page()
