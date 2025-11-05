import streamlit as st
import json
import time
import requests

# =====================================================
# CONFIGURAÇÕES INICIAIS
# =====================================================

st.set_page_config(page_title="QuizIA", layout="centered")

st.title("🤖 QuizIA - Geração de Questões Inteligentes")
st.markdown("Crie quizzes automáticos com base no conteúdo de um livro, artigo ou apostila 📘")

# =====================================================
# FUNÇÕES AUXILIARES
# =====================================================

def dividir_em_chunks(texto, tamanho_chunk=2000, sobreposicao=200):
    """
    Divide o texto em partes menores mantendo contexto.
    """
    palavras = texto.split()
    chunks = []
    for i in range(0, len(palavras), tamanho_chunk - sobreposicao):
        parte = " ".join(palavras[i:i + tamanho_chunk])
        chunks.append(parte)
    return chunks


def gerar_questoes_com_ia(conteudo, disciplina):
    """
    Chama a API (simulada aqui) para gerar questões de múltipla escolha com justificativa.
    Retorna uma lista de dicionários contendo:
    pergunta, opcoes, resposta_correta, justificativa
    """
    # Simulação de chamada de API
    # Você pode substituir esta parte por uma chamada real ao seu endpoint LLM.
    time.sleep(2)
    questoes = [
        {
            "pergunta": "Qual é o objetivo principal da camada de transporte em redes de computadores?",
            "opcoes": [
                "A) Fornecer comunicação lógica entre processos de aplicação em hosts diferentes",
                "B) Garantir a transmissão física de dados através do meio",
                "C) Definir endereçamento IP para roteamento de pacotes",
                "D) Controlar o acesso múltiplo ao meio físico"
            ],
            "resposta_correta": "A",
            "justificativa": "A camada de transporte fornece comunicação lógica entre processos de aplicação em hosts diferentes, conforme descrito no livro."
        },
        {
            "pergunta": "Qual destes protocolos oferece entrega confiável e controle de congestionamento?",
            "opcoes": [
                "A) UDP",
                "B) IP",
                "C) TCP",
                "D) ARP"
            ],
            "resposta_correta": "C",
            "justificativa": "O protocolo TCP é orientado à conexão e fornece entrega confiável e controle de congestionamento."
        }
    ]
    return questoes


def gerar_questoes_para_todo_texto(texto, disciplina):
    """
    Gera questões para cada chunk do texto, com barra de progresso e feedback visual.
    """
    chunks = dividir_em_chunks(texto)
    todas_questoes = []

    barra = st.progress(0)
    total_chunks = len(chunks)

    for i, chunk in enumerate(chunks):
        st.info(f"🔍 Gerando questões do trecho {i+1}/{total_chunks}...")
        questoes_chunk = gerar_questoes_com_ia(chunk, disciplina)
        todas_questoes.extend(questoes_chunk)
        barra.progress((i + 1) / total_chunks)
        time.sleep(0.3)

    st.success(f"✅ {len(todas_questoes)} questões geradas com sucesso!")
    return todas_questoes


# =====================================================
# INTERFACE DO APP
# =====================================================

aba = st.sidebar.radio("📚 Menu", ["Gerar Questões", "Responder Quiz"])

# =====================================================
# ABA 1: GERAR QUESTÕES
# =====================================================
if aba == "Gerar Questões":
    st.header("🧠 Gerar Questões a partir de um Texto")

    disciplina = st.text_input("Digite o nome da disciplina:")
    texto = st.text_area("Cole aqui o conteúdo (ex: texto do livro, resumo ou apostila):", height=300)

    if st.button("Gerar Questões"):
        if not texto.strip() or not disciplina.strip():
            st.warning("⚠️ Por favor, preencha todos os campos.")
        else:
            questoes = gerar_questoes_para_todo_texto(texto, disciplina)

            st.session_state["questoes_geradas"] = questoes
            st.success("Questões geradas e salvas na sessão. Vá até a aba **Responder Quiz** para testá-las.")


# =====================================================
# ABA 2: RESPONDER QUIZ
# =====================================================
elif aba == "Responder Quiz":
    st.header("🎯 Responder Quiz Interativo")

    if "questoes_geradas" not in st.session_state:
        st.warning("⚠️ Nenhum quiz gerado ainda. Vá até a aba 'Gerar Questões' primeiro.")
    else:
        questoes = st.session_state["questoes_geradas"]
        pontuacao = 0

        for i, q in enumerate(questoes):
            st.markdown(f"### {i+1}. {q['pergunta']}")
            escolha = st.radio("Escolha uma opção:", q["opcoes"], key=f"q{i}")

            # Captura a letra da resposta escolhida
            letra_escolhida = escolha.split(")")[0]
            if letra_escolhida == q["resposta_correta"]:
                st.success("✅ Resposta correta!")
                pontuacao += 1
            else:
                st.error(f"❌ Resposta incorreta. A correta é **{q['resposta_correta']}**.")
            st.markdown(f"📘 *Justificativa:* {q['justificativa']}")
            st.divider()

        total = len(questoes)
        st.subheader("📊 Resultado Final")
        st.write(f"Você acertou **{pontuacao}/{total}** questões ({pontuacao/total*100:.1f}%)")

        if pontuacao / total == 1:
            st.balloons()
            st.success("🎉 Excelente! Você acertou todas!")
        elif pontuacao / total >= 0.7:
            st.info("💪 Bom desempenho! Continue assim.")
        else:
            st.warning("📚 Estude um pouco mais e tente novamente!")
