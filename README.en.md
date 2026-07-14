<div align="center">

# Career Star Map · CPA-Agent

**An Open-Source Multi-Agent Career Planning Platform for University Students**

</br>
<em>面向大学生全周期职业规划的多智能体决策平台</em>

[![License](https://img.shields.io/badge/License-AGPL--3.0-blue?style=flat-square&logo=gnu&logoColor=white)](./LICENSE)
[![GitHub Stars](https://img.shields.io/github/stars/WHaoxM/cpa-agent?style=flat-square&color=DAA520)](https://github.com/WHaoxM/cpa-agent/stargazers)
[![GitHub Forks](https://img.shields.io/github/forks/WHaoxM/cpa-agent?style=flat-square)](https://github.com/WHaoxM/cpa-agent/network)
[![GitHub Issues](https://img.shields.io/github/issues/WHaoxM/cpa-agent?style=flat-square)](https://github.com/WHaoxM/cpa-agent/issues)
[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![Vue](https://img.shields.io/badge/Vue-3.5-42b883?style=flat-square&logo=vue.js&logoColor=white)](https://vuejs.org/)
[![Flask](https://img.shields.io/badge/Flask-3.x-000000?style=flat-square&logo=flask&logoColor=white)](https://flask.palletsprojects.com/)
[![Docker](https://img.shields.io/badge/Docker-Ready-2496ED?style=flat-square&logo=docker&logoColor=white)](https://www.docker.com/)

[![Live Demo](https://img.shields.io/badge/Live%20Demo-GitHub%20Pages-6E40C9?style=flat-square&logo=github&logoColor=white)](https://whaoxm.github.io/cpa-agent-demo/)

[English](./README.en.md) | [中文](./README.md)

</div>

## ⚡ Overview

**CPA-Agent** (Career Planning Agent) is an open-source intelligent career planning platform for university students. It combines resume parsing, a four-dimensional capability portrait, job–person matching on a career knowledge graph, and multi-agent orchestration to produce explainable, traceable career recommendations.

> You only need to: Upload a PDF resume, or describe your background and goals in conversation</br>
> CPA-Agent will return: A structured four-dimensional capability portrait, explainable matching results, and actionable career development reports

### Our Vision

CPA-Agent is dedicated to breaking through the limitations of traditional career guidance, letting every "what if" see its outcome:

- **For Students**: Personalized diagnosis based on real resumes and job data, not generic career advice
- **For Universities**: Batch parsing, standardized portraits and report generation, suitable for career centers to use at scale
- **For Researchers**: L0–L4 pipeline, dual-engine Agent, and NSLE entity extraction all have clear module boundaries, facilitating paper experiments and ablation studies

From resume input to career report, every step has Agent Trace observability, and recommendation results can be traced back to graph nodes and matching weights—rather than a black-box "you should be a product manager."

## 🌐 Live Demo

Experience the frontend online demo (primarily uses Mock data, no backend required):

👉 **[https://whaoxm.github.io/cpa-agent-demo/](https://whaoxm.github.io/cpa-agent-demo/)**

**Demo Accounts:**

| Role | Username | Password |
|------|----------|----------|
| Student | 123456 | 123456 |
| Admin | 123456 | 123456 |

## ✨ Core Highlights

Two capabilities define the fundamental difference between CPA-Agent and general chatbots:

- 🧭 **L0–L4 Layered Pipeline.** Resume parsing, graph building, portrait generation, job matching, and report generation are each independent yet can be chained. Each layer has clear inputs/outputs and `pipeline_task` state tracking, suitable for batch processing and async tasks in university scenarios.
- 🤖 **Dual-Engine Agent + 12 Tools.** LangGraph (primary) with native function calling + MySQL Checkpoint persistence; ReAct (fallback) compatible with LLMs that don't support tool calls. The Agent can invoke graph queries, semantic search, pipeline triggers, and more—maintaining clear boundaries between reasoning and deterministic business logic.

## 🔄 Workflow

1. **Resume Parsing (L0)**: PDF/Docx → Layout Detection → NER Entity Extraction → `student_profile`
2. **Graph Building (L1)**: Resume/JD Entities → Neo4j Nodes & Relationships → Skill-Job-Career Path Network
3. **Portrait Generation (L2)**: Student ID + Skills → Four-Dimensional Scoring (D1 Basic/D2 Skills/D3 Literacy/D4 Potential) → `student_portrait_4d`
4. **Job Matching (L3)**: Student Portrait + Job Portrait → Weighted Match Score + Skill Gap + Promotion Path
5. **Report Generation (L4)**: Student + Target Job → Six-Chapter `career_report` (with Learning Path & Assessment Cycle)

## 🚀 Quick Start

### Option 1: Online Demo (Recommended for First Use)

Visit the [Live Demo](https://whaoxm.github.io/cpa-agent-demo/) directly and log in with the demo account to experience all frontend features.

### Option 2: Local Deployment (Full Features)

#### Prerequisites

| Tool | Version | Description | Check |
|------|---------|-------------|-------|
| **Python** | 3.11+ | Backend runtime | `python --version` |
| **Node.js** | ^20.19.0 \|\| >=22.12.0 | Frontend runtime | `node -v` |
| **Conda** | Recommended | Environment management | `conda --version` |
| **Docker** | Latest | Database dependencies | `docker --version` |

#### 1. Configure Environment Variables

```bash
# Copy the example configuration file
cp .env.example .env
# Windows PowerShell:
# Copy-Item .env.example .env

# Edit the .env file, fill in the required API keys and database passwords
```

**Required Environment Variables:**

```env
# LLM API Configuration (supports any OpenAI SDK compatible LLM API)
# Recommended: DeepSeek / Qwen / Kimi / Ollama
LLM_API_KEY=your_api_key
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL_NAME=deepseek-chat

# Database Configuration
MYSQL_PASSWORD=your_password
NEO4J_PASSWORD=your_password
REDIS_URL=redis://localhost:6379/0

# Vector Database (uses Lite embedded mode by default, no extra deployment needed)
MILVUS_URI=./milvus_data/milvus.db
EMBEDDING_MODEL=BAAI/bge-m3
```

#### 2. Start Database Dependencies

```bash
# Start MySQL + Neo4j + Redis
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d mysql neo4j redis
```

#### 3. Start Backend

```bash
conda activate mkg  # or your environment name
cd backend
pip install -r requirements.txt

# Initialize database and graph
python scripts/neo4j/init_neo4j.py
python scripts/milvus/init_milvus.py

# Start service
python run.py
# → http://localhost:5001
```

#### 4. Start Frontend

```bash
cd vue-kecheng
npm install
Copy-Item .env.example .env.local
# In .env.local: VITE_API_BASE_URL=http://127.0.0.1:5001
npm run dev
# → http://localhost:5173
```

### Option 3: Docker Deployment (Database Layer)

```bash
# Development environment (databases only)
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d

# Production environment (with backend)
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

**Port Mapping:**
- Frontend: `5173` (dev) / `3000` (Docker)
- Backend API: `5001`
- MySQL: `13306`
- Neo4j Bolt: `17687` / HTTP: `17668`
- Redis: `16379`

## 🏗️ Architecture Overview

```
Vue 3 Frontend (vue-kecheng)
        │ HTTP / SSE
        ▼
Flask Backend (backend/app)
  API Routes → Service Layer → Agent Engine (LangGraph / ReAct)
                        │
                        ▼
              Tools (12) ──→ MySQL / Neo4j / Redis / Milvus
                        │
                        ▼
              L0–L4 Pipeline (Resume→Graph→Portrait→Match→Report)
```

**L0–L4 Pipeline:**

| Layer | Input | Output |
|-------|-------|--------|
| L0 | PDF / Text Resume | `student_profile` |
| L1 | JD / Resume Text | Neo4j Nodes & Relationships |
| L2 | Student ID + Skills | `student_portrait_4d` |
| L3 | Student + Job Portrait | Match Score, Skill Gap, Career Path |
| L4 | Student + Target Job | Six-Chapter `career_report` |

## 📊 Four-Dimensional Portrait & Job Matching

**Competitiveness = D1×0.25 + D2×0.35 + D3×0.20 + D4×0.20**

| Dimension | Meaning | Example |
|-----------|---------|---------|
| **D1 Basic Requirements** | Degree, major, certificates (hard constraints) | Bachelor / CS-related |
| **D2 Professional Skills** | Skill coverage and proficiency | Java, Spring Boot, MySQL |
| **D3 Professional Literacy** | Soft skills, projects and internships | Teamwork, project delivery |
| **D4 Development Potential** | Competitions, papers, innovation output | Lanqiao Cup, open-source contributions |

The matching engine combines weighted scoring, skill gap analysis, and promotion/transfer paths in Neo4j, outputting not just a "match score" but also "what's missing and how to fill the gap."

## 🛠️ Tech Stack

### Backend

- **Python 3.11+** / Flask 3.x
- **LangGraph + LangChain** - Agent orchestration (primary engine)
- **ReAct** - Compatible fallback engine
- **SQLAlchemy** - ORM
- **PyMilvus Lite** - Embedded vector database

### Frontend

- **Vue 3.5** + TypeScript
- **Vite 7** - Build tool
- **Element Plus** - UI component library
- **D3.js / ECharts / Three.js** - Data visualization
- **Pinia** - State management
- **GSAP** - Animation engine

### Data Layer

- **MySQL 8.0** - Business entities and relational data
- **Neo4j 5.x** - Skill-job-career path knowledge graph
- **Redis 7** - Session management and caching
- **Milvus Lite** - Vector search (BGE-M3, dim=1024)

### AI Models

- **LLM** - OpenAI-compatible API (DeepSeek / Qwen / Kimi / Ollama, etc.)
- **Embedding** - BAAI/bge-m3 (multilingual, 1024-dim)
- **Resume Parsing** - MinerU + YOLOv10 layout detection + Qwen3-SFT NER

## 💡 A Concrete Example

A student uploads a resume and says to the AI assistant:

> I'm a junior CS student. I've done two Spring Boot backend projects and I'm familiar with MySQL and Redis. I want to do backend development after graduation, but I'm not sure whether to aim for a big tech company or gain experience at a startup first.

After pipeline and Agent collaboration, the system may return:

```text
[Portrait Summary] D2 Professional Skills 72/100 · D4 Development Potential 68/100
[Matched Job] Java Backend Developer (Match 81%)
[Skill Gap]
  · Needs improvement: Distributed systems, message queues (Kafka/RabbitMQ)
  · Already has: Java, Spring Boot, MySQL, Redis
[Career Path]
  Junior Backend → Mid Backend (2-3 years) → Architect / Tech Lead
[Learning Suggestions]
  1. Complete a message queue实战 project (4-6 weeks recommended)
  2. Supplement system design fundamentals (see learning path module for courses)
```

This is not generic career advice, but actionable recommendations based on graph data, job portraits, and skill gap analysis.

## 🎯 Use Cases

- **University Career Centers** — Batch parse student resumes, generate standardized capability portraits and reports
- **Career Planning Courses / Competitions** — Demonstrate the complete engineering implementation of "Knowledge Graph + RAG + Agent"
- **Individual Developers** — Build your own career knowledge base, connect to real job data, do personalized matching
- **Research Reproduction** — L0–L4 pipeline, NSLE entity extraction, dual-engine Agent all have clear module boundaries, facilitating paper experiments and ablation studies

## 🤝 Contributing

Issues and Pull Requests are welcome. Suggested workflow:

1. Fork this repository, pull the latest code from `main`
2. Complete changes locally, verify backend API or frontend `npm run type-check`
3. Submit a PR, explaining the scope of changes and testing methods

For larger changes (Schema changes, new pipeline layers, Agent tools), please open an Issue first for discussion. The schemas for MySQL / Neo4j / Milvus are frozen (v1.0); only **additions** of fields or nodes are allowed, breaking changes are prohibited.

## 🗺️ Roadmap

Planned capabilities not yet fully implemented:

- Cross-session style memory: Portraits and recommendations gradually personalize with use
- History enhancement: Search, re-matching, report regeneration
- Local ASR / voice input career consultation
- More industry JD data sources and automatic graph expansion
- Extended frontend-backend API integration (currently connected: career landscape, AI assistant demo, career navigation Pipeline tasks; other complex pages still partially use Mock)

## 📄 Related Documentation

| Document | Description |
|----------|-------------|
| [CODE_WIKI.md](./CODE_WIKI.md) | Complete code architecture and runtime guide |
| [CONTEXT.md](./CONTEXT.md) | Domain glossary and project decision context |
| [DEPLOY.md](./DEPLOY.md) | Detailed deployment instructions |
| [vue-kecheng/README.md](./vue-kecheng/README.md) | Frontend modules, routes, and demo accounts |
| [vue-kecheng/DEPLOY.md](./vue-kecheng/DEPLOY.md) | GitHub Pages deployment |
| [docs/](./docs/) | ADRs, runbooks, specs, and guides |

## 📄 Acknowledgments

- Thanks to Sichuan University of Science & Engineering (SUSE) for supporting the project
- Thanks to all students who participated in design, development, and testing
- Thanks to the open-source community and competition ecosystem for providing job data and knowledge base resources
- Resume parsing references the open-source work of MinerU and YOLOv10
- Agent design patterns reference the open-source implementations of LangGraph and ReAct

## 📜 License

This project is open-sourced under the [GNU Affero General Public License v3.0](./LICENSE).

This means:
- ✅ You can freely use, modify, and distribute this project
- ✅ Network service usage must also open-source the modified code
- ❌ Cannot be used in closed-source commercial products

We chose AGPL-3.0 to ensure that all improvements based on this project are returned to the community, especially to protect scenarios deployed as network services where modified versions must remain open-source.

## 📈 Star History

<a href="https://www.star-history.com/#WHaoxM/cpa-agent&Date">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=WHaoxM/cpa-agent&type=Date&theme=dark" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=WHaoxM/cpa-agent&type=Date" />
   <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=WHaoxM/cpa-agent&type=Date" />
 </picture>
</a>

---

<div align="center">

**CPA-Agent** · Maintained by the SUSE team

</div>
