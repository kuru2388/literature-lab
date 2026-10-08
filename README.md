# Literature Lab 🔬📚

> **Agentic AI Academic Literature Research Assistant & Interactive Reading Studio**  
> Turn any project idea or thesis question into a rigorous literature review, discover defensible research gaps, extract datasets, and master papers with an AI reading tutor.

[![Python 3.10+](https://img.shields.io/badge/Python-3.10%20%7C%203.11%20%7C%203.12-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-009688.svg)](https://fastapi.tiangolo.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Free Tier Friendly](https://img.shields.io/badge/Google%20AI%20Studio-100%25%20Free%20Tier-success.svg)](https://aistudio.google.com/apikey)

---

## 🌟 Key Highlights

- **11-Stage Multi-Agent Pipeline:** Planner, Verifier, Retriever, Abstract Ranker, PDF Downloader, Technical Extractor, Gap Discovery, Re-Ranker, Fit Verifier, Dataset Cataloger, and Report Writer.
- **4 Live Academic Search Repositories:** Parallel live search across **arXiv**, **OpenAlex**, **Semantic Scholar**, and **PubMed** (last 7 years, deduplicated, zero hallucinations).
- **Interactive Reading Studio (`/read`):** Embedded full-text PDF canvas with a 3-pass staged reading strategy (Relevance &rarr; Technical Depth &rarr; Survey Matrix & Snowballing).
- **AI Reading Tutor Copilot:** Real-time contextual reading coach that teaches how to evaluate methodology and spot limitations, equipped with strict off-topic guardrails.
- **100% Free Tier Supported:** Seamlessly powered by **Google Gemini Flash** (up to 1,500 free requests/day with no credit card required) or **OpenAI GPT-4o**.
- **Private & Local First:** Runs entirely on your machine with SQLite. API keys and notes stay local in `data/` and are never sent to external third-party servers.

---

## 🏗️ Multi-Agent Architecture

```
                       [User Research Idea]
                                │
                                ▼
 1. Planner Agent ─────────────► Unpacks domains & generates search tags
                                │
                                ▼
 2. Verifier Agent ────────────► Audits keywords (approves relevant, drops noisy)
                                │
                                ▼
 3. Searcher Agent ────────────► Queries arXiv, OpenAlex, Semantic Scholar, PubMed
                                │
                                ▼
 4. Abstract Ranker ───────────► Scores 100 candidate papers & picks top shortlist
                                │
                                ▼
 5. Downloader Agent ──────────► Downloads open-access PDFs to local storage
                                │
                                ▼
 6. Extractor Agent ───────────► Parses PDF text (intro, method, findings, limits)
                                │
                                ▼
 7. Gap Discovery Agent ───────► Cross-analyzes papers to detect genuine research gaps
                                │
                                ▼
 8. Deep Re-Ranker ────────────► Re-ranks based on full technical depth & gap value
                                │
                                ▼
 9. Relevance Verifier ────────► Confirms fit against your specific research idea
                                │
                                ▼
10. Dataset Mining Agent ──────► Extracts benchmarks, corpora, and dataset links
                                │
                                ▼
11. Report Writer Agent ───────► Compiles literature survey memo & reading roadmap
```

---

## 📋 Prerequisites

Before installing, ensure you have:
- **Python 3.10, 3.11, or 3.12** installed ([python.org](https://www.python.org/downloads/))
- **Git** installed ([git-scm.com](https://git-scm.com/))
- An API key from **Google AI Studio (100% Free)** or **OpenAI**:
  - [Get a free Google Gemini API Key](https://aistudio.google.com/apikey) *(Recommended: Gemini Flash has 1,500 free daily requests)*
  - [Get an OpenAI API Key](https://platform.openai.com/api-keys)

---

## 🚀 Quickstart & Installation

You can set up Literature Lab in less than 2 minutes using either the **1-Click Automated Scripts** or **Step-by-Step Terminal Commands**.

---

### Option 1: One-Click Quickstart (Recommended)

#### 🪟 Windows
1. Double-click **`setup.bat`** (or open Command Prompt and run `setup.bat`).  
   *This automatically detects Python, sets up `.venv`, upgrades pip, installs all packages from `requirements.txt`, and generates `.env`.*
2. Double-click **`run.bat`** to start the server.
3. Open your browser at **[http://localhost:8001](http://localhost:8001)**.

#### 🍎 macOS / 🐧 Linux
1. In your terminal, make scripts executable and run setup:
   ```bash
   chmod +x setup.sh run.sh
   ./setup.sh
   ```
2. Launch the application:
   ```bash
   ./run.sh
   ```
3. Open your browser at **[http://localhost:8001](http://localhost:8001)**.

---

### Option 2: Step-by-Step Manual Setup

#### 🪟 Windows (PowerShell)
```powershell
# Step 1: Clone the repository
git clone https://github.com/kuru2388/literature-lab.git
cd literature-lab

# Step 2: Create a virtual environment
python -m venv .venv

# Step 3: Activate the virtual environment
# (If script execution is disabled, first run: Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass)
.\.venv\Scripts\Activate.ps1

# Step 4: Upgrade pip to latest version
python -m pip install --upgrade pip

# Step 5: Install all required project packages
pip install -r requirements.txt

# Step 6: Create local environment file from example
copy .env.example .env

# Step 7: Launch the server
python main.py
```

#### 🪟 Windows (Command Prompt / CMD)
```cmd
:: Step 1: Clone and navigate
git clone https://github.com/kuru2388/literature-lab.git
cd literature-lab

:: Step 2: Create virtual environment
python -m venv .venv

:: Step 3: Activate virtual environment
.\.venv\Scripts\activate.bat

:: Step 4: Upgrade pip & install packages
python -m pip install --upgrade pip
pip install -r requirements.txt

:: Step 5: Initialize environment configuration
copy .env.example .env

:: Step 6: Start Literature Lab
python main.py
```

#### 🍎 macOS / 🐧 Linux (Bash / Zsh)
```bash
# Step 1: Clone and navigate
git clone https://github.com/kuru2388/literature-lab.git
cd literature-lab

# Step 2: Create a virtual environment
python3 -m venv .venv

# Step 3: Activate the virtual environment
source .venv/bin/activate

# Step 4: Upgrade pip to latest version
pip install --upgrade pip

# Step 5: Install all required project packages
pip install -r requirements.txt

# Step 6: Initialize environment configuration
cp .env.example .env

# Step 7: Launch the server
python3 main.py
```

---

### 📦 Package Management Commands

If you are developing or adding new packages to Literature Lab, use these standard commands:

| Action | Windows (PowerShell/CMD) | macOS / Linux |
| :--- | :--- | :--- |
| **Activate Virtual Env** | `.\.venv\Scripts\activate` | `source .venv/bin/activate` |
| **Install New Package** | `pip install <package-name>` | `pip install <package-name>` |
| **Update Single Package** | `pip install --upgrade <package-name>` | `pip install --upgrade <package-name>` |
| **Reinstall All Dependencies** | `pip install --force-reinstall -r requirements.txt` | `pip install --force-reinstall -r requirements.txt` |
| **Update `requirements.txt`** | `pip freeze > requirements.txt` | `pip freeze > requirements.txt` |
| **Deactivate Virtual Env** | `deactivate` | `deactivate` |

---

### Option 3: Running Directly with Uvicorn (Live Reload)

During active development, launch Uvicorn with auto-reload:
```bash
uvicorn main:app --reload --host 127.0.0.1 --port 8001
```

Once running, open your web browser at:
👉 **[http://localhost:8001](http://localhost:8001)**

---

## ⚙️ Configuration & API Keys

You can configure your API keys in two ways:

### Method A: Through the Web UI (Easiest)
1. Open [http://localhost:8001/settings](http://localhost:8001/settings) in your browser.
2. Under **Gemini Flash (Free Quota)**, paste your Google AI Studio key and click **Save Gemini key**.
3. *(Optional)* Add your **OpenAI API key** if you prefer `gpt-4o`.
4. Keys are securely saved locally to `data/settings.json` (git-ignored).

### Method B: Environment File (`.env`)
Copy the provided `.env.example` to `.env`:

```bash
cp .env.example .env
```

Edit `.env` with your preferred editor:
```env
# Primary LLM Options (Pick at least one)
GEMINI_API_KEY=AIzaSy...your-gemini-key
OPENAI_API_KEY=sk-...your-openai-key

# Optional: Higher rate limits for academic search APIs
CONTACT_EMAIL=you@example.com                # OpenAlex polite pool (Higher rate limits)
SEMANTIC_SCHOLAR_API_KEY=your-s2-key         # Semantic Scholar API key
NCBI_API_KEY=your-ncbi-key                   # PubMed higher rate limit (10 req/sec)
```

---

## 📖 How to Use

### 1. Run Literature Research
1. Navigate to `http://localhost:8001`.
2. Enter your project idea (e.g., *"Lightweight GUI grounding agents on mobile devices"*).
3. Select paper count (e.g., 8 papers) and preferred model (Gemini Flash or GPT-4o).
4. Click **Run research**. The live agent pipeline will execute each step with real-time status updates.

### 2. Reading Studio (`/read`)
- Click **Read** on any gathered paper to enter the Reading Studio.
- Use the **Staged Reading Strategy** to evaluate relevance in Pass 1, unpack architecture & baselines in Pass 2, and snowball citations in Pass 3.
- Use the **AI Reading Tutor** to ask technique questions on dataset size, methodology, or baseline fairness.
- Highlight text in the PDF to bookmark annotations and export notes as a formatted PDF memo.

### 3. Literature Matrix & Gap Check Export
- Download synthesized survey matrices in **Excel (`.xlsx`)** or **CSV (`.csv`)** format.
- Run interactive **Gap Verification** to validate your novelty against the existing literature.

---

## 📂 Project Structure

```text
literature-lab/
├── main.py                     # FastAPI application entrypoint & API routes
├── requirements.txt            # Python dependencies
├── .env.example                # Example environment configuration
├── data/                       # Local SQLite database, downloads & cached files (git-ignored)
│   ├── tasks.db
│   ├── settings.json
│   └── papers/
├── src/
│   ├── orchestrator.py         # Multi-agent execution coordinator & state machine
│   ├── llm.py                  # AISuite, OpenAI & Gemini LLM abstraction layer
│   ├── tutor.py                # AI Reading Tutor copilot & refusal guardrails
│   ├── reader.py               # PDF outline extraction & section mapper
│   ├── matrix.py               # Literature Survey Matrix generator (CSV/Excel)
│   ├── db.py                   # SQLite database models & task queries
│   ├── agents/                 # Specialized Pipeline Agents
│   │   ├── planner.py          # Agent 1: Research query & keyword planner
│   │   ├── verifier.py         # Agent 2 & 9: Keyword & paper relevance auditor
│   │   ├── searcher.py         # Agent 3: Multi-repository paper searcher
│   │   ├── ranker.py           # Agent 4 & 8: Abstract & deep technical ranker
│   │   ├── downloader.py       # Agent 5: Open-access PDF downloader
│   │   ├── extractor.py        # Agent 6: PyMuPDF section findings extractor
│   │   ├── gaps.py             # Agent 7: Research gap discovery agent
│   │   ├── datasets.py         # Agent 10: Evaluation dataset mining agent
│   │   └── writer.py           # Agent 11: Final literature memo author
│   └── tools/                  # Academic API Integrations
│       ├── arxiv.py            # arXiv API integration
│       ├── openalex.py         # OpenAlex REST API client
│       ├── s2.py               # Semantic Scholar bulk search client
│       └── pubmed.py           # NCBI Entrez PubMed client
├── static/                     # Vanilla CSS stylesheets & client JavaScript
│   ├── app.css                 # Responsive design system
│   ├── read-app.js             # Reading Studio client controller
│   └── nav.js                  # Mobile drawer navigation controller
└── templates/                  # Jinja2 HTML Templates
    ├── index.html              # Main research dashboard
    ├── read.html               # 3-Panel Reading Studio & Copilot
    └── settings.html           # Settings & API Keys configuration
```

---

## 🛠️ Troubleshooting

<details>
<summary><b>Port 8001 is already in use</b></summary>

If port 8001 is occupied, specify an alternative port:
```bash
uvicorn main:app --reload --port 8002
```
Or find and terminate the occupying process on Windows:
```powershell
Get-Process -Id (Get-NetTCPConnection -LocalPort 8001).OwningProcess | Stop-Process
```
</details>

<details>
<summary><b>PyMuPDF / fitz installation errors</b></summary>

Ensure your `pip` is up to date:
```bash
pip install --upgrade pip setuptools wheel
pip install pymupdf
```
</details>

<details>
<summary><b>arXiv rate limits or timeout</b></summary>

arXiv enforces a polite 3-second delay between search requests. The Searcher agent automatically handles pacing, but if arXiv undergoes maintenance, the pipeline will fallback on OpenAlex, Semantic Scholar, and PubMed.
</details>

## 🗺️ Roadmap & Future Plans

Have ideas or want to build something next? Here is what is planned for upcoming releases:

- [ ] **Google Docs-Style Research Paper Studio:** Rich-text document writer where users upload any reference paper, automatically extract its structure & style template (IEEE, ACM, Nature, APA), and write their own paper directly in that format with AI co-writing.
- [ ] **Local LLM Support (Ollama / Llama.cpp):** Run 100% offline without needing any cloud API keys.
- [ ] **Audio Paper Briefings:** Text-to-speech podcast summaries generated from extracted PDF findings.

> **Want to propose a new feature?** Open a discussion or feature request in [GitHub Issues](https://github.com/kuru2388/literature-lab/issues)!

---

## 🤝 Contributing

Contributions, bug reports, and suggestions are warmly welcomed! See [CONTRIBUTING.md](file:///c:/Users/ASUS/Desktop/my%20project/research/CONTRIBUTING.md) for full developer guidelines.

1. **Fork the Repository** on GitHub.
2. **Create a Feature Branch**:
   ```bash
   git checkout -b feature/amazing-feature
   ```
3. **Commit your changes**:
   ```bash
   git commit -m "feat: add amazing feature"
   ```
4. **Push to the branch**:
   ```bash
   git push origin feature/amazing-feature
   ```
5. **Open a Pull Request** with a detailed summary of your additions.

---

## 👤 Author & Contact

**Nimalan Kurushangar**
- 💼 **LinkedIn:** [linkedin.com/in/nimalan-kurushangar](https://www.linkedin.com/in/nimalan-kurushangar)
- 📧 **Email:** [nimalankurusangar@gmail.com](mailto:nimalankurusangar@gmail.com)
- 🐙 **GitHub:** [@kuru2388](https://github.com/kuru2388)

---

## 📜 License

Distributed under the **MIT License**. See [LICENSE](file:///c:/Users/ASUS/Desktop/my%20project/research/LICENSE) for the full text.

---

## 💡 Acknowledgements

- Built with [FastAPI](https://fastapi.tiangolo.com/), [AISuite](https://github.com/andrewyng/aisuite), and [PyMuPDF](https://pymupdf.readthedocs.io/).
- Academic data provided by [arXiv](https://arxiv.org/), [OpenAlex](https://openalex.org/), [Semantic Scholar](https://www.semanticscholar.org/), and [PubMed](https://pubmed.ncbi.nlm.nih.gov/).



