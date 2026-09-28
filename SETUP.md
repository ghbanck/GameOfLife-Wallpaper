# Guia rápido de instalação

## 1. Compilar

Abra o PowerShell nesta pasta e rode:

```powershell
python installation.py
```

Ao final ele pergunta se deve **iniciar com o Windows** e se deve **abrir agora**.
O executável fica em `dist\GameOfLifeWallpaper.exe` — é o único arquivo necessário
(copie para onde quiser; `config.json`, log e o mundo salvo ficam ao lado dele).

Recompilar depois de editar o código: rode `python installation.py` de novo. Ele fecha a
cópia que estiver rodando, mantém `dist\config.json` e o mundo salvo, e substitui só o `.exe`.

## 2. Usar

- O papel de parede aparece atrás dos ícones e um planador verde aparece na bandeja.
- `Ctrl+Alt+Shift+G` ou um clique no planador: modo desenho (painel de controle + desenhar
  na área de trabalho). De novo, `Esc` ou **Concluir**: volta a ser só papel de parede.
- Clique direito no planador: pausar, sopa nova, próxima paleta, **Mundos** (os salvos e os três
  de exemplo: relógio digital, jardim de osciladores, desfile de naves), ocultar, iniciar com o
  Windows, abrir configurações/log, sair.
- No painel, aba **Desenhar**: a biblioteca tem ~4.800 padrões (Life Lexicon + LifeWiki), com
  ícone, busca e uma ficha animada ao passar o mouse. Arquivos `.rle`/`.cells` colocados em
  **Minha pasta** aparecem em *Meus padrões*.
- Aba **Mundo**: escolha um mundo na lista e clique em **Carregar**; digite um nome e clique em
  **Salvar** para guardar o mundo atual.

## 3. Iniciar com o Windows (ou parar)

Pelo menu da bandeja, pelo painel (aba **Visual**) ou pela linha de comando:

```powershell
dist\GameOfLifeWallpaper.exe --install-startup
dist\GameOfLifeWallpaper.exe --uninstall-startup
```

## 4. Desinstalar

1. Menu da bandeja → desmarque **Iniciar com o Windows** → **Sair**.
2. Apague a pasta. Nada é instalado no sistema além da entrada opcional de inicialização.

## Problemas?

`golwall.log` ao lado do executável explica o que ele fez (menu da bandeja → **Abrir o log**),
e `GameOfLifeWallpaper.exe --diagnose` mostra o que ele detecta na sua área de trabalho.
Mais detalhes no `README.md`.
