# Vinted Deal Hunter — Discord V1

Versão experimental para guardar links de pesquisas da Vinted Portugal e tentar notificar novas publicações através do HTML público. **Não foi comprovado que os resultados da Vinted estejam disponíveis em HTML hoje**. Quando estiverem indisponíveis, o bot não consegue monitorizar; não tenta contornar CAPTCHAs, autenticação, bloqueios ou outras proteções.

## Instalação (Windows)

1. Cria uma aplicação e bot no [Discord Developer Portal](https://discord.com/developers/applications), convida-o para o servidor com permissões `bot`, `applications.commands`, `Send Messages` e `Embed Links`.
2. Instala Python 3.11+ e, nesta pasta, executa `python -m pip install -r requirements.txt`.
3. No PowerShell, configura **apenas na tua máquina**:
   ```powershell
   $env:DISCORD_BOT_TOKEN="TOKEN_PRIVADO"
   $env:DISCORD_GUILD_ID="ID_DO_SERVIDOR"
   python bot.py
   ```
4. No Discord usa `/teste`, depois `/adicionar`, colando o URL completo da pesquisa.
5. Consulta `/pesquisas` e usa `/remover` para gerir as pesquisas.

Os dados são guardados no ficheiro SQLite local `deals.db`, que **não deve ser enviado para o GitHub**.

## Funcionamento

- Mantém os filtros `brand_ids[]`, `search_text`, `price_to`, etc. da URL; força ordenação pelos mais recentes.
- A primeira leitura guarda os anúncios existentes, sem enviar notificações antigas.
- Nas leituras seguintes, envia apenas anúncios cujos IDs não foram vistos.
- Intervalo padrão: 300 segundos, mínimo 60 segundos; altera com `CHECK_INTERVAL_SECONDS`.
- Se a Vinted devolver HTTP 403/429, JavaScript sem resultados ou bloquear acesso, o bot regista a situação e **não envia alertas**. Não oferece monitorização em tempo real garantida.
- O PC precisa de estar ligado; o GitHub é apenas o repositório de código.

## Segurança e limitações

O acesso automatizado à Vinted pode não ser permitido pelos termos da plataforma. Usa apenas meios autorizados, não partilhes tokens e não executes o projeto com credenciais de acesso à Vinted. Não existe API oficial pública de pesquisa integrada. A autenticidade dos artigos não é verificada por este bot.

O código desta pasta é uma implementação nova e independente, não uma cópia do repositório de terceiros mencionado na conversa. Foi isolado para não substituir o código anterior do teu repositório.
