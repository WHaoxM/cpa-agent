-- learning_path_template 种子 (零基础→初级岗位路径)
USE cpa_agent;
SET NAMES utf8mb4;

INSERT IGNORE INTO learning_path_template (path_id, target_role, target_level, steps, total_weeks, description) VALUES
('zero_to_junior_frontend', '前端开发', '初级',
 '[{"order":1,"skill":"HTML","weeks":2,"resources":[{"name":"MDN Web Docs","url":"https://developer.mozilla.org/zh-CN/","type":"doc"}]},{"order":2,"skill":"CSS","weeks":2,"resources":[{"name":"CSS Tricks","url":"https://css-tricks.com/","type":"doc"}]},{"order":3,"skill":"JavaScript","weeks":4,"resources":[{"name":"JavaScript.info","url":"https://zh.javascript.info/","type":"doc"}]},{"order":4,"skill":"Vue.js","weeks":3,"resources":[{"name":"Vue官方教程","url":"https://cn.vuejs.org/tutorial/","type":"doc"}]},{"order":5,"skill":"TypeScript","weeks":2,"resources":[{"name":"TS Handbook","url":"https://www.typescriptlang.org/zh/docs/handbook/","type":"doc"}]}]',
 13, '零基础→初级前端开发，13周从HTML到Vue项目实战'),

('zero_to_junior_backend', '后端开发', '初级',
 '[{"order":1,"skill":"Python","weeks":4,"resources":[{"name":"Python官方教程","url":"https://docs.python.org/zh-cn/3/tutorial/","type":"doc"}]},{"order":2,"skill":"SQL","weeks":2,"resources":[{"name":"SQLZoo","url":"https://sqlzoo.net/","type":"interactive"}]},{"order":3,"skill":"MySQL","weeks":3,"resources":[{"name":"MySQL教程","url":"https://www.runoob.com/mysql/mysql-tutorial.html","type":"doc"}]},{"order":4,"skill":"Django","weeks":3,"resources":[{"name":"Django官方教程","url":"https://docs.djangoproject.com/zh-hans/","type":"doc"}]},{"order":5,"skill":"REST API设计","weeks":2,"resources":[{"name":"RESTful API设计指南","url":"https://restfulapi.net/","type":"doc"}]}]',
 14, '零基础→初级后端开发(Python路线)，14周从Python到Django API'),

('zero_to_junior_data_analyst', '数据分析师', '初级',
 '[{"order":1,"skill":"SQL","weeks":2,"resources":[{"name":"SQLZoo","url":"https://sqlzoo.net/","type":"interactive"}]},{"order":2,"skill":"Excel","weeks":2,"resources":[{"name":"Excel技巧","url":"https://www.excel-easy.com/","type":"doc"}]},{"order":3,"skill":"Python","weeks":3,"resources":[{"name":"Python数据分析","url":"https://pandas.pydata.org/docs/","type":"doc"}]},{"order":4,"skill":"统计学","weeks":4,"resources":[{"name":"可汗学院统计","url":"https://zh.khanacademy.org/","type":"video"}]},{"order":5,"skill":"Tableau","weeks":2,"resources":[{"name":"Tableau培训","url":"https://www.tableau.com/zh-cn/learn","type":"course"}]}]',
 13, '零基础→初级数据分析师，13周从SQL到可视化报表'),

('zero_to_junior_agent', 'AI智能体工程师', '初级',
 '[{"order":1,"skill":"Python","weeks":4,"resources":[{"name":"Python官方教程","url":"https://docs.python.org/zh-cn/3/tutorial/","type":"doc"}]},{"order":2,"skill":"Prompt Engineering","weeks":2,"resources":[{"name":"Prompt工程指南","url":"https://www.promptingguide.ai/zh","type":"doc"}]},{"order":3,"skill":"LangChain","weeks":3,"resources":[{"name":"LangChain文档","url":"https://python.langchain.com/","type":"doc"}]},{"order":4,"skill":"RAG","weeks":3,"resources":[{"name":"RAG从零到一","url":"https://docs.langchain.com/","type":"doc"}]},{"order":5,"skill":"向量数据库","weeks":2,"resources":[{"name":"Milvus教程","url":"https://milvus.io/docs/","type":"doc"}]}]',
 14, '零基础→初级AI智能体工程师，14周从Python到Agent应用'),

('zero_to_junior_mlops', 'MLOps工程师', '初级',
 '[{"order":1,"skill":"Python","weeks":4,"resources":[{"name":"Python官方教程","url":"https://docs.python.org/zh-cn/3/tutorial/","type":"doc"}]},{"order":2,"skill":"Docker","weeks":2,"resources":[{"name":"Docker入门","url":"https://docs.docker.com/get-started/","type":"doc"}]},{"order":3,"skill":"Linux","weeks":3,"resources":[{"name":"Linux命令行","url":"https://linuxcommand.org/","type":"doc"}]},{"order":4,"skill":"MLflow","weeks":2,"resources":[{"name":"MLflow文档","url":"https://mlflow.org/docs/latest/quickstart.html","type":"doc"}]},{"order":5,"skill":"Kubernetes","weeks":4,"resources":[{"name":"K8s交互教程","url":"https://kubernetes.io/zh-cn/docs/tutorials/","type":"doc"}]}]',
 15, '零基础→初级MLOps工程师，15周从Python到模型部署流水线'),

('zero_to_junior_test', '测试开发', '初级',
 '[{"order":1,"skill":"Python","weeks":4,"resources":[{"name":"Python官方教程","url":"https://docs.python.org/zh-cn/3/tutorial/","type":"doc"}]},{"order":2,"skill":"接口测试","weeks":2,"resources":[{"name":"Postman教程","url":"https://learning.postman.com/","type":"doc"}]},{"order":3,"skill":"Selenium","weeks":3,"resources":[{"name":"Selenium文档","url":"https://selenium-python.readthedocs.io/","type":"doc"}]},{"order":4,"skill":"自动化框架","weeks":3,"resources":[{"name":"Pytest教程","url":"https://docs.pytest.org/","type":"doc"}]},{"order":5,"skill":"CI/CD","weeks":2,"resources":[{"name":"GitHub Actions","url":"https://docs.github.com/cn/actions","type":"doc"}]}]',
 14, '零基础→初级测试开发，14周从Python到自动化测试框架'),

('zero_to_junior_devops', 'DevOps工程师', '初级',
 '[{"order":1,"skill":"Linux","weeks":3,"resources":[{"name":"Linux命令行","url":"https://linuxcommand.org/","type":"doc"}]},{"order":2,"skill":"Docker","weeks":2,"resources":[{"name":"Docker入门","url":"https://docs.docker.com/get-started/","type":"doc"}]},{"order":3,"skill":"Kubernetes","weeks":4,"resources":[{"name":"K8s交互教程","url":"https://kubernetes.io/zh-cn/docs/tutorials/","type":"doc"}]},{"order":4,"skill":"CI/CD","weeks":2,"resources":[{"name":"GitHub Actions","url":"https://docs.github.com/cn/actions","type":"doc"}]},{"order":5,"skill":"Python/Go","weeks":4,"resources":[{"name":"Go官方教程","url":"https://go.dev/tour/","type":"interactive"}]}]',
 15, '零基础→初级DevOps工程师，15周从Linux到CI/CD流水线'),

('zero_to_junior_security', '网络安全工程师', '初级',
 '[{"order":1,"skill":"Linux","weeks":3,"resources":[{"name":"Linux命令行","url":"https://linuxcommand.org/","type":"doc"}]},{"order":2,"skill":"网络协议","weeks":3,"resources":[{"name":"计算机网络","url":"https://www.bilibili.com/","type":"video"}]},{"order":3,"skill":"Python","weeks":3,"resources":[{"name":"Python安全编程","url":"https://docs.python.org/","type":"doc"}]},{"order":4,"skill":"网络安全","weeks":4,"resources":[{"name":"OWASP","url":"https://owasp.org/","type":"doc"}]},{"order":5,"skill":"渗透测试","weeks":4,"resources":[{"name":"Hack The Box","url":"https://www.hackthebox.com/","type":"interactive"}]}]',
 17, '零基础→初级网络安全工程师，17周从Linux到渗透测试');
