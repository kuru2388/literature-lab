# Contributing to Literature Lab 🔬

Thank you for your interest in contributing to Literature Lab! We welcome contributions of all kinds: bug fixes, new search providers, UI improvements, documentation enhancements, and feature proposals.

---

## 🛠️ How Collaborators Contribute (Step-by-Step)

### 1. Fork & Clone
1. Fork this repository on GitHub by clicking the **Fork** button at the top right.
2. Clone your fork locally:
   ```bash
   git clone https://github.com/<your-github-username>/literature-lab.git
   cd literature-lab
   ```

### 2. Set Up Your Local Dev Environment
Follow the setup instructions using Python 3.10+:
```bash
# Windows:
setup.bat

# macOS / Linux:
chmod +x setup.sh run.sh
./setup.sh
```

### 3. Create a Topic Branch
Always make your changes in a separate feature or bugfix branch, never directly on `main`:
```bash
git checkout -b feature/your-feature-name
# or for bugfixes:
git checkout -b fix/issue-description
```

### 4. Make Your Changes & Test Locally
- Follow the existing code style (clean modular Python with type hints).
- For frontend UI, stick to Vanilla CSS in `static/app.css` to maintain fast rendering.
- Verify the server starts and endpoints respond without errors:
  ```bash
  python main.py
  ```

### 5. Commit & Push
Write clear, descriptive commit messages:
```bash
git add .
git commit -m "feat: add support for Ollama local models"
git push origin feature/your-feature-name
```

### 6. Submit a Pull Request (PR)
1. Go to your fork on GitHub and click **Compare & pull request**.
2. Give your PR a clear title and describe what changed and why.
3. Link any relevant issues (e.g. `Fixes #12`).
4. Wait for review and address any feedback!

---

## 🗺️ Where to Add Future Plans & Features

Collaborators and maintainers track future ideas in three main places:

1. **In the Codebase ([`README.md`](file:///c:/Users/ASUS/Desktop/my%20project/research/README.md)):**  
   Add high-level milestones to the **`## 🗺️ Roadmap & Future Plans`** checklist section.
2. **In GitHub Issues:**  
   Label feature proposals with `enhancement` or `good first issue` so newcomers can easily find tasks to pick up.
3. **In GitHub Projects / Milestones:**  
   Organize planned releases into Kanban boards (e.g., *To Do*, *In Progress*, *Done*).

---

## 📜 Code of Conduct
Please ensure all discussions, code reviews, and interactions remain respectful, collaborative, and constructive.
