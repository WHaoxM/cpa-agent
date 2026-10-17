<div align="center">

# Career Star Map · CPA-Agent

**An Open-Source Multi-Agent Career Planning Platform for University Students**

</br>
<em>面向大学生全周期职业规划的多智能体决策平台</em>

[![License](https://img.shields.io/badge/License-AGPL--3.0-blue?style=flat-square&logo=gnu&logoColor=white)](./LICENSE)
[![GitHub Stars](https://img.shields.io/github/stars/WHaoxM/cpa-agent?style=flat-square&color=DAA520)](https://github.com/WHaoxM/cpa-agent/stargazers)
[![GitHub Forks](https://img.shields.io/github/forks/WHaoxM/cpa-agent?style=flat-square)](https://github.com/WHaoxM/cpa-agent/network)
[![GitHub Issues](https://img.shields.io/github/issues/WHaoxM/cpa-agent?style=flat-square)](https://github.com/WHaoxM/cpa-agent/issues)
[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![Vue](https://img.shields.io/badge/Vue-3.5-42b883?style=flat-square&logo=vue.js&logoColor=white)](https://vuejs.org/)
[![Flask](https://img.shields.io/badge/Flask-3.x-000000?style=flat-square&logo=flask&logoColor=white)](https://flask.palletsprojects.com/)
[![Docker](https://img.shields.io/badge/Docker-Dev%20DB%20Stack-2496ED?style=flat-square&logo=docker&logoColor=white)](./DEPLOY.md)

[![Live Demo](https://img.shields.io/badge/Live%20Demo-GitHub%20Pages-6E40C9?style=flat-square&logo=github&logoColor=white)](https://whaoxm.github.io/cpa-xingtu/)
[![Frontend](https://img.shields.io/badge/Frontend-cpa--xingtu-42b883?style=flat-square&logo=vue.js&logoColor=white)](https://github.com/WHaoxM/cpa-xingtu)

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
- **For Researchers**: L0–L4 pipeline, multi-mode Agent, and NSLE entity extraction all have clear module boundaries, facilitating paper experiments and ablation studies

From resume input to career report, every step has Agent Trace observability, and recommendation results can be traced back to graph nodes and matching weights—rather than a black-box "you should be a product manager."

## 🌐 Live Demo

Experience the frontend online demo (primarily uses Mock data, no backend required):

👉 **[https://whaoxm.github.io/cpa-xingtu/](https://whaoxm.github.io/cpa-xingtu/)**

**Demo Accounts:**

| Role | Username | Password |
|------|----------|----------|
| Student | student001 | 123456 |
| Admin | admin001 | 123456 |

These accounts belong to the frontend Mock/Pages demo only. Strict backend mode has no built-in demo accounts.

## ✨ Core Highlights

Two capabilities define the fundamental difference between CPA-Agent and general chatbots:

- 🧭 **L0–L4 Layered Pipeline.** Resume parsing, graph building, portrait generation, job matching, and report generation have separate boundaries and `pipeline_task` state tracking. Dedicated L0 resume ingestion and L3 matching use durable Celery delivery; confirmed-portrait/diagnostic extensions require a checkout containing their `0006`/`0007` migrations and matching code, as noted below. This does not make every legacy pipeline entry point reliably asynchronous.
- 🤖 **Multi-Mode Agent + 13 Tools.** The default runtime is a **Supervisor orchestrator** (Ask / Agent / Experts interaction modes; Plan steps + blackboard + per-student memory store + PreToolUse gating). The legacy LangGraph monolith / ReAct engines are disabled by default and enabled only via a demo switch. The Agent can invoke graph queries, semantic search, pipeline triggers, and more—maintaining clear boundaries between reasoning and deterministic business logic.

## 🔄 Workflow

1. **Resume Parsing (L0)**: PDF/Docx → Layout Detection → NER Entity Extraction → `student_profile`
2. **Graph Building (L1)**: Resume/JD Entities → Neo4j Nodes & Relationships → Skill-Job-Career Path Network
3. **Portrait Generation (L2)**: Student ID + Skills → Four-Dimensional Scoring (D1 Basic/D2 Skills/D3 Literacy/D4 Potential) → `student_portrait_4d`
4. **Job Matching (L3)**: Student Portrait + Job Portrait → Weighted Match Score + Skill Gap + Promotion Path
5. **Report Generation (L4)**: Student + Target Job → Six-Chapter `career_report` (with Learning Path & Assessment Cycle)

## 🚀 Reproduce Locally

Choose the scope before starting services:

| Path | Steps | What it verifies |
|---|---|---|
| Frontend-only demo | Clone the separate [cpa-xingtu](https://github.com/WHaoxM/cpa-xingtu) repo; use Node `^20.19.0 || >=22.12.0`; run `npm ci` and `npm run dev:mock` there | Fixture/Mock UI at `http://localhost:5173`; no backend, databases, or LLM |
| Backend startup | Copy `.env.example` to `.env`; install `backend/requirements.txt` with Python 3.10; start MySQL/Neo4j/Redis, run Alembic, then run `python -B run.py` from `backend/` | `/health` and route registration; **not** asynchronous or business-result acceptance |
| Real frontend/backend integration | Set `CPA_DEMO_MODE=false`, a strong `SECRET_KEY`, and LLM credentials; start the `cpa.pipeline`, `cpa.media`, and `cpa.graph` Celery worker and Beat as described in [DEPLOY.md](DEPLOY.md); run `npm run dev:backend` in the frontend repo | Register/login and check owner-scoped resume, task, and final result state; trusted job matching additionally needs published job data |

Local reproduction steps and production blockers are in [DEPLOY.md](DEPLOY.md); the phased image plan is in [Production Image Hardening](docs/specs/production-image-hardening.md). A fresh copy of `.env.example` defaults to demo mode; the maintainer's private `.env` may use strict mode. Production requires the actual frontend HTTPS Origin, not `*`, and the `SameSite=Lax` account cookie should be served through a same-origin or at least same-site HTTPS entry. The frontend is a separate repository. Dev Compose runs databases only. The single-host production **candidate** has an explicit migration job, Gunicorn HTTP, worker, Beat, and a cold-volume snapshot/restore-to-new-project tool, and publishes no application host port by default. It still lacks a verified immutable registry image, a locked dependency set, HTTPS, a real Docker-volume recovery drill, and full business acceptance; it is not a one-command release. New workbench portrait/diagnostic capabilities also require a checkout that actually contains the corresponding backend code and `0006`/`0007` migrations; `current=head` alone cannot prove an absent migration is present.

## 🏗️ Architecture Overview

```
Vue 3 Frontend (cpa-xingtu)
        │ HTTP / SSE
        ▼
Flask Backend (backend/app)
  API Routes → Service Layer → Agent Runtime (Supervisor orchestration; Ask/Agent/Experts modes)
                        │
                        ▼
              Tools (13) ──→ MySQL / Neo4j / Redis / Milvus
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

- **Python 3.10+** / Flask 3.x
- **Supervisor multi-mode Agent runtime** - Ask / Agent / Experts orchestration (legacy LangGraph monolith / ReAct engines disabled by default)
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

The following is **illustrative product output**, not a deterministic result from a fresh clone. Real results depend on a confirmed resume, model services, graph data, and reviewed published jobs:

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
- **Research Reproduction** — L0–L4 pipeline, NSLE entity extraction, multi-mode Agent all have clear module boundaries, facilitating paper experiments and ablation studies

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
| [Current work](./docs/current-work.md) | Dated entry point for active work and verification gaps |
| [Historical Code Wiki](./docs/archive/2026-10/CODE_WIKI.md) | Archived 2026-06 code map; not a current architecture or runtime guide |
| [CONTEXT.md](./CONTEXT.md) | Domain glossary and project decision context |
| [DEPLOY.md](./DEPLOY.md) | Detailed deployment instructions |
| [cpa-xingtu (frontend repo)](https://github.com/WHaoxM/cpa-xingtu) | Frontend modules, routes, demo accounts, and Pages deploy |
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
