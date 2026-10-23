"""Focused, transactional import of two reviewed document resources.

Defaults to a read-only diff. Shared development data requires separate review
of this diff and backup; --apply --confirm-database must name the target exactly.
Existing course IDs and chapter sequences are permanent identities, not ordering
knobs. Never deletes chapters, progress, or unrelated catalog records.
"""
import argparse
import json
from pathlib import Path
import sys

from sqlalchemy import text

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
from app.services.db.mysql_client import MySQLClient

MANIFEST = BACKEND / 'config' / 'curated_learning_resources.json'


def import_resources(mysql, *, apply=False):
    manifest = json.loads(MANIFEST.read_text(encoding='utf-8'))
    changes = []
    with mysql.engine.begin() as conn:
        for resource in manifest['resources']:
            cid = resource['course_id']
            description = resource['description'] + '\n资料来源：' + resource['source'] + '\n资料核对日期：' + manifest['reviewed_on']
            values = {'course_id': cid, 'title': resource['title'], 'category': resource['category'], 'description': description}
            row = conn.execute(text('SELECT course_id,title,category,description FROM course WHERE course_id=:course_id' + (' FOR UPDATE' if apply else '')), values).mappings().first()
            if row and not str(row['description'] or '').startswith('官方文档整理 ·'):
                raise ValueError(f'ID collision: {cid}; existing content is not managed by this import')
            action = 'create' if not row else 'unchanged' if dict(row) == values else 'update'
            changes.append({'kind': 'course', 'course_id': cid, 'action': action, 'before': dict(row) if row else None, 'after': values})
            if apply and action == 'create':
                conn.execute(text('INSERT INTO course(course_id,title,category,description) VALUES (:course_id,:title,:category,:description)'), values)
            elif apply and action == 'update':
                conn.execute(text('UPDATE course SET title=:title,category=:category,description=:description WHERE course_id=:course_id'), values)
            for chapter in resource['chapters']:
                params = {'course_id': cid, 'sequence': chapter['sequence'], 'title': chapter['title'], 'content_summary': chapter['summary'], 'video_url': chapter['url']}
                existing = conn.execute(text('SELECT id,title,content_summary,video_url FROM chapter WHERE course_id=:course_id AND sequence=:sequence' + (' FOR UPDATE' if apply else '')), params).mappings().all()
                if len(existing) > 1:
                    raise ValueError(f'Ambiguous chapter identity: {cid}/{chapter["sequence"]}')
                old = dict(existing[0]) if existing else None
                action = 'create' if old is None else 'unchanged' if all(old[k] == params[k] for k in ('title','content_summary','video_url')) else 'update'
                changes.append({'kind': 'chapter', 'course_id': cid, 'sequence': chapter['sequence'], 'action': action, 'before': old,
                                'after': {k: params[k] for k in ('title','content_summary','video_url')}})
                if apply and action == 'create':
                    conn.execute(text('INSERT INTO chapter(course_id,sequence,title,content_summary,video_url) VALUES (:course_id,:sequence,:title,:content_summary,:video_url)'), params)
                elif apply and action == 'update':
                    conn.execute(text('UPDATE chapter SET title=:title,content_summary=:content_summary,video_url=:video_url WHERE id=:id'), {**params, 'id': old['id']})
    return {'applied': apply, 'reviewed_on': manifest['reviewed_on'], 'changes': changes}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--confirm-database')
    args = parser.parse_args()
    if args.apply and args.confirm_database != args.database:
        parser.error('--apply requires --confirm-database matching --database exactly')
    mysql = MySQLClient(database=args.database)
    try:
        print(json.dumps(import_resources(mysql, apply=args.apply), ensure_ascii=False, default=str, indent=2))
    finally:
        mysql.close()


if __name__ == '__main__':
    main()
