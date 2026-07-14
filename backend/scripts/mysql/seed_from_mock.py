"""
从前端 mock 数据 JSON 导入 MySQL
补充 job_posting 表 + match_result 表 + course 表扩展
用法: python -m scripts.mysql.seed_from_mock
"""
import sys, os, json, re
# seed_from_mock.py 位于 backend/scripts/mysql/, 需上溯 3 级到 backend/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..'))

from app.utils.logger import get_logger
logger = get_logger('cpa-agent.seed_from_mock')

# mock_data/ 位于 backend/scripts/mock_data/
MOCK_DIR = os.path.join(os.path.dirname(__file__), '..', 'mock_data')


def parse_salary_range(s: str):
    """解析 '6K–10K' / '15K-25K' / '20K以上' → (min, max)"""
    if not s:
        return None, None
    nums = re.findall(r'(\d+)\s*K?', s.replace('–', '-').replace('—', '-'))
    if len(nums) >= 2:
        return int(nums[0]) * 1000, int(nums[1]) * 1000
    if len(nums) == 1:
        v = int(nums[0]) * 1000
        return v, int(v * 1.5)
    return None, None


def level_to_experience(level: str):
    """level → 最低经验年限"""
    return {'intern': 0, 'junior': 1, 'mid': 3, 'senior': 5, 'lead': 8, 'expert': 10}.get(level, 0)


def level_to_degree(level: str):
    """level → 学历要求"""
    if level in ('intern', 'junior'):
        return '本科'
    if level in ('mid', 'senior'):
        return '本科'
    return '硕士'


def seven_dim_to_4d(seven_dim):
    """前端七维 → 后端四维匹配分数"""
    skill = seven_dim.get('专业技能', 0)
    cert = seven_dim.get('证书资质', 0)
    innovation = seven_dim.get('创新能力', 0)
    learning = seven_dim.get('学习能力', 0)
    stress = seven_dim.get('抗压能力', 0)
    communication = seven_dim.get('沟通能力', 0)
    internship = seven_dim.get('实习经验', 0)

    d1 = round(cert * 0.5 + internship * 0.5, 1)           # 基础要求
    d2 = round(skill, 1)                                     # 职业技能
    d3 = round(stress * 0.5 + communication * 0.5, 1)       # 职业素养
    d4 = round(innovation * 0.5 + learning * 0.5, 1)        # 发展潜力
    return d1, d2, d3, d4


def match_level_from_score(score):
    """total_score → match_level"""
    if score >= 80:
        return '极佳'
    if score >= 65:
        return '良好'
    if score >= 50:
        return '一般'
    return '不匹配'


def seed_job_postings(mysql, portraits):
    """从 JOB_PORTRAITS 生成 job_posting 记录"""
    inserted = 0
    for p in portraits:
        job_id = p['id']
        salary_min, salary_max = parse_salary_range(p.get('salaryRange', ''))
        posting_json = json.dumps({
            'title': p['title'],
            'level': p['level'],
            'lineId': p['lineId'],
            'stack': p.get('stack', ''),
            'desc': p.get('desc', ''),
            'keySkills': p.get('keySkills', []),
            'salaryRange': p.get('salaryRange', ''),
        }, ensure_ascii=False)

        sql = """
        INSERT INTO job_posting (
            job_id, position_name, position_normalized, job_category,
            company_name, company_industry, company_size,
            degree_requirement, experience_min,
            salary_min, salary_max,
            posting_json, is_it_position
        ) VALUES (
            :job_id, :position_name, :position_normalized, :job_category,
            :company_name, :company_industry, :company_size,
            :degree_requirement, :experience_min,
            :salary_min, :salary_max,
            :posting_json, 1
        )
        ON DUPLICATE KEY UPDATE position_name=VALUES(position_name)
        """
        try:
            mysql.execute_write(sql, {
                'job_id': job_id,
                'position_name': p['title'],
                'position_normalized': p['title'],
                'job_category': p.get('lineId', ''),
                'company_name': None,
                'company_industry': None,
                'company_size': None,
                'degree_requirement': level_to_degree(p['level']),
                'experience_min': level_to_experience(p['level']),
                'salary_min': salary_min,
                'salary_max': salary_max,
                'posting_json': posting_json,
            })
            inserted += 1
        except Exception as e:
            logger.warning(f"job_posting 插入失败 {job_id}: {e}")

    logger.info(f"job_posting: {inserted}/{len(portraits)} 条插入/更新")
    return inserted


def seed_match_results(mysql, portraits, student_id='student_001'):
    """为指定学生生成 match_result 记录 (含七维 d5-d10)"""
    inserted = 0
    for p in portraits:
        job_portrait_id = p['id']
        seven_dim = p.get('sevenDim', {})
        d1, d2, d3, d4 = seven_dim_to_4d(seven_dim)
        # 七维原始分数直接落库 (d5=证书, d6=创新, d7=学习, d8=抗压, d9=沟通, d10=实习)
        d5 = float(seven_dim.get('证书资质', 0))
        d6 = float(seven_dim.get('创新能力', 0))
        d7 = float(seven_dim.get('学习能力', 0))
        d8 = float(seven_dim.get('抗压能力', 0))
        d9 = float(seven_dim.get('沟通能力', 0))
        d10 = float(seven_dim.get('实习经验', 0))

        match_score = p.get('matchScore', 0.5)
        total_score = round(match_score * 100, 1)

        d1_pass = 1 if d1 >= 40 else 0
        skill_coverage = round(min(d2 / 100, 1.0), 2)
        is_matched = 1 if total_score >= 50 else 0
        match_level = match_level_from_score(total_score)

        result_json = json.dumps({
            'student_id': student_id,
            'job_portrait_id': job_portrait_id,
            'position_name': p['title'],
            'match_score': match_score,
            'seven_dim': seven_dim,
            'key_skills': p.get('keySkills', []),
        }, ensure_ascii=False)

        sql = """
        INSERT INTO match_result (
            student_id, job_portrait_id,
            d1_match, d2_match, d3_match, d4_match, total_score,
            d5_match, d6_match, d7_match, d8_match, d9_match, d10_match,
            d1_pass, skill_coverage, is_matched, match_level,
            result_json
        ) VALUES (
            :student_id, :job_portrait_id,
            :d1, :d2, :d3, :d4, :total,
            :d5, :d6, :d7, :d8, :d9, :d10,
            :d1_pass, :skill_coverage, :is_matched, :match_level,
            :result_json
        )
        ON DUPLICATE KEY UPDATE
            d1_match=VALUES(d1_match), d2_match=VALUES(d2_match),
            d3_match=VALUES(d3_match), d4_match=VALUES(d4_match),
            d5_match=VALUES(d5_match), d6_match=VALUES(d6_match),
            d7_match=VALUES(d7_match), d8_match=VALUES(d8_match),
            d9_match=VALUES(d9_match), d10_match=VALUES(d10_match),
            total_score=VALUES(total_score),
            is_matched=VALUES(is_matched), match_level=VALUES(match_level),
            result_json=VALUES(result_json)
        """
        try:
            mysql.execute_write(sql, {
                'student_id': student_id,
                'job_portrait_id': job_portrait_id,
                'd1': d1, 'd2': d2, 'd3': d3, 'd4': d4,
                'total': total_score,
                'd5': d5, 'd6': d6, 'd7': d7, 'd8': d8, 'd9': d9, 'd10': d10,
                'd1_pass': d1_pass,
                'skill_coverage': skill_coverage,
                'is_matched': is_matched,
                'match_level': match_level,
                'result_json': result_json,
            })
            inserted += 1
        except Exception as e:
            logger.warning(f"match_result 插入失败 {student_id}/{job_portrait_id}: {e}")

    logger.info(f"match_result: {inserted}/{len(portraits)} 条插入/更新 (student={student_id}, 含七维)")
    return inserted


def seed_courses(mysql, courses):
    """扩展 course 表（跳过已存在的 course_id）"""
    inserted = 0
    skipped = 0
    for c in courses:
        course_id = c['course_id']
        # 检查是否已存在
        existing = mysql.execute(
            "SELECT course_id FROM course WHERE course_id = :cid",
            {'cid': course_id}
        )
        if existing:
            skipped += 1
            continue

        sql = """
        INSERT INTO course (
            course_id, title, category, description,
            difficulty, duration_hours, instructor, thumbnail_url
        ) VALUES (
            :course_id, :title, :category, :description,
            :difficulty, :duration_hours, :instructor, :thumbnail_url
        )
        """
        try:
            mysql.execute_write(sql, {
                'course_id': course_id,
                'title': c['title'],
                'category': c.get('category', ''),
                'description': c.get('description', ''),
                'difficulty': c.get('difficulty', 'beginner'),
                'duration_hours': c.get('duration_hours', 0),
                'instructor': c.get('instructor', '未知讲师'),
                'thumbnail_url': c.get('thumbnail_url', ''),
            })
            inserted += 1
        except Exception as e:
            logger.warning(f"course 插入失败 {course_id}: {e}")

    logger.info(f"course: {inserted} 条新增, {skipped} 条跳过 (已存在)")
    return inserted


def seed_job_portraits_4d(mysql, portraits):
    """为每个 mock 岗位画像生成 job_portrait_4d 记录 (含七维 d5-d10 权重)"""
    inserted = 0
    for p in portraits:
        portrait_id = p['id']
        position_name = p['title']
        seven_dim = p.get('sevenDim', {})
        key_skills = p.get('keySkills', [])

        # 七维权重 (根据该岗位对七维的重视程度，简化为统一权重)
        d5_weight = 0.10  # 证书资质
        d6_weight = 0.10  # 创新能力
        d7_weight = 0.15  # 学习能力
        d8_weight = 0.15  # 抗压能力
        d9_weight = 0.15  # 沟通能力
        d10_weight = 0.15  # 实习经验

        portrait_json = json.dumps({
            'portrait_id': portrait_id,
            'position_name': position_name,
            'level': p.get('level', ''),
            'lineId': p.get('lineId', ''),
            'stack': p.get('stack', ''),
            'salaryRange': p.get('salaryRange', ''),
            'desc': p.get('desc', ''),
            'keySkills': key_skills,
            'sevenDim': seven_dim,
            'matchScore': p.get('matchScore', 0),
        }, ensure_ascii=False)

        sql = """
        INSERT INTO job_portrait_4d (
            portrait_id, position_name, jd_count,
            required_degree, major_strict, required_skill_cnt,
            d1_weight, d2_weight, d3_weight, d4_weight,
            d5_weight, d6_weight, d7_weight, d8_weight, d9_weight, d10_weight,
            portrait_json
        ) VALUES (
            :portrait_id, :position_name, :jd_count,
            :required_degree, :major_strict, :required_skill_cnt,
            :d1_weight, :d2_weight, :d3_weight, :d4_weight,
            :d5_weight, :d6_weight, :d7_weight, :d8_weight, :d9_weight, :d10_weight,
            :portrait_json
        )
        ON DUPLICATE KEY UPDATE
            position_name=VALUES(position_name),
            jd_count=VALUES(jd_count),
            required_skill_cnt=VALUES(required_skill_cnt),
            d5_weight=VALUES(d5_weight), d6_weight=VALUES(d6_weight),
            d7_weight=VALUES(d7_weight), d8_weight=VALUES(d8_weight),
            d9_weight=VALUES(d9_weight), d10_weight=VALUES(d10_weight),
            portrait_json=VALUES(portrait_json)
        """
        try:
            mysql.execute_write(sql, {
                'portrait_id': portrait_id,
                'position_name': position_name,
                'jd_count': 10,  # R-05 要求 ≥10
                'required_degree': level_to_degree(p.get('level', 'junior')),
                'major_strict': 0,
                'required_skill_cnt': len(key_skills),
                'd1_weight': 0.25, 'd2_weight': 0.35,
                'd3_weight': 0.20, 'd4_weight': 0.20,
                'd5_weight': d5_weight, 'd6_weight': d6_weight,
                'd7_weight': d7_weight, 'd8_weight': d8_weight,
                'd9_weight': d9_weight, 'd10_weight': d10_weight,
                'portrait_json': portrait_json,
            })
            inserted += 1
        except Exception as e:
            logger.warning(f"job_portrait_4d 插入失败 {portrait_id}: {e}")

    logger.info(f"job_portrait_4d: {inserted}/{len(portraits)} 条插入/更新 (含七维权重)")
    return inserted


def seed_skill_gaps(mysql, portraits, student_id='student_001'):
    """为指定学生 + 每个岗位生成 skill_gap 记录"""
    inserted = 0
    # 学生已掌握的技能 (模拟)
    student_skills = {'Vue 3', 'TypeScript', 'JavaScript', 'HTML/CSS', 'Git', 'Element Plus', 'Vite'}

    for p in portraits:
        job_portrait_id = p['id']
        required_skills = p.get('keySkills', [])

        critical = []  # 关键缺失
        quick_learn = []  # 快速可学

        for sk in required_skills:
            # 简化匹配: 名称完全相等视为已掌握
            if sk in student_skills:
                continue
            # 基础技能归为快速可学
            if any(k in sk.lower() for k in ['html', 'css', 'git', 'rest', 'api']):
                quick_learn.append(sk)
            else:
                critical.append(sk)

        total_gap = len(critical) + len(quick_learn)
        gap_json = json.dumps({
            'student_id': student_id,
            'job_portrait_id': job_portrait_id,
            'position_name': p['title'],
            'critical_gaps': critical,
            'quick_learn_gaps': quick_learn,
            'student_skills': list(student_skills),
            'required_skills': required_skills,
        }, ensure_ascii=False)

        sql = """
        INSERT INTO skill_gap (
            student_id, job_portrait_id,
            critical_count, quick_learn_count, total_gap_count,
            gap_json
        ) VALUES (
            :student_id, :job_portrait_id,
            :critical_count, :quick_learn_count, :total_gap_count,
            :gap_json
        )
        ON DUPLICATE KEY UPDATE
            critical_count=VALUES(critical_count),
            quick_learn_count=VALUES(quick_learn_count),
            total_gap_count=VALUES(total_gap_count),
            gap_json=VALUES(gap_json)
        """
        try:
            mysql.execute_write(sql, {
                'student_id': student_id,
                'job_portrait_id': job_portrait_id,
                'critical_count': len(critical),
                'quick_learn_count': len(quick_learn),
                'total_gap_count': total_gap,
                'gap_json': gap_json,
            })
            inserted += 1
        except Exception as e:
            logger.warning(f"skill_gap 插入失败 {student_id}/{job_portrait_id}: {e}")

    logger.info(f"skill_gap: {inserted}/{len(portraits)} 条插入/更新 (student={student_id})")
    return inserted


def seed_student_assessments(mysql, student_id='student_001'):
    """为指定学生生成 student_self_assessment 自评记录"""
    import uuid

    # 模拟自评数据
    assessed_skills = [
        {"name": "Vue 3", "level": "掌握", "score": 75},
        {"name": "TypeScript", "level": "熟悉", "score": 65},
        {"name": "JavaScript", "level": "掌握", "score": 78},
        {"name": "HTML/CSS", "level": "精通", "score": 85},
        {"name": "Git", "level": "熟悉", "score": 70},
    ]
    assessed_abilities = [
        {"name": "学习能力", "score": 80},
        {"name": "抗压能力", "score": 55},
        {"name": "沟通能力", "score": 60},
        {"name": "创新能力", "score": 35},
    ]
    weak_skills = ["性能优化", "SSR", "微前端", "Node.js"]
    recommended_roles = ["fe-junior", "fe-intern", "fe-vis-junior"]

    assessment_id = f"asm_{uuid.uuid4().hex[:12]}"

    sql = """
    INSERT INTO student_self_assessment
        (assessment_id, student_id, assessed_skills, assessed_abilities,
         weak_skills, recommended_roles)
    VALUES
        (:assessment_id, :student_id, :assessed_skills, :assessed_abilities,
         :weak_skills, :recommended_roles)
    """
    try:
        mysql.execute_write(sql, {
            'assessment_id': assessment_id,
            'student_id': student_id,
            'assessed_skills': json.dumps(assessed_skills, ensure_ascii=False),
            'assessed_abilities': json.dumps(assessed_abilities, ensure_ascii=False),
            'weak_skills': json.dumps(weak_skills, ensure_ascii=False),
            'recommended_roles': json.dumps(recommended_roles, ensure_ascii=False),
        })
        logger.info(f"student_self_assessment: 1 条插入 (student={student_id}, asm_id={assessment_id})")
        return 1
    except Exception as e:
        logger.warning(f"student_self_assessment 插入失败: {e}")
        return 0


def main():
    # 读取 JSON
    portraits_path = os.path.join(MOCK_DIR, 'job_portraits.json')
    courses_path = os.path.join(MOCK_DIR, 'courses.json')

    with open(portraits_path, 'r', encoding='utf-8') as f:
        portraits = json.load(f)
    with open(courses_path, 'r', encoding='utf-8') as f:
        courses = json.load(f)

    logger.info(f"读取 {len(portraits)} 条岗位画像, {len(courses)} 条课程")

    # 连接 MySQL
    from app.services.db import get_mysql_client
    mysql = get_mysql_client()

    # 1. job_posting 表
    logger.info("═══ 1/6 导入 job_posting ═══")
    seed_job_postings(mysql, portraits)

    # 2. job_portrait_4d 表 (新增 - 解决 /match/trend 中 position_name 为空的问题)
    logger.info("═══ 2/6 导入 job_portrait_4d (含七维权重) ═══")
    seed_job_portraits_4d(mysql, portraits)

    # 3. match_result 表 (为 student_001 生成)
    logger.info("═══ 3/6 导入 match_result (student_001, 含七维 d5-d10) ═══")
    seed_match_results(mysql, portraits, student_id='student_001')

    # 4. skill_gap 表 (新增 - 解决 /skill-gap 端点 404)
    logger.info("═══ 4/6 导入 skill_gap (student_001) ═══")
    seed_skill_gaps(mysql, portraits, student_id='student_001')

    # 5. student_self_assessment 表 (新增 - 解决 /assessment 端点无数据)
    logger.info("═══ 5/6 导入 student_self_assessment (student_001) ═══")
    seed_student_assessments(mysql, student_id='student_001')

    # 6. course 表扩展
    logger.info("═══ 6/6 扩展 course 表 ═══")
    seed_courses(mysql, courses)

    logger.info("✅ 全部导入完成!")


if __name__ == '__main__':
    main()
