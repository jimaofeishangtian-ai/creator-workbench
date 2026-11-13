import pytest

from app import services


def seed(store, identity, source='favorites', title='可灵AI生成了一段画面'):
    services.ingest(store, {'id': identity, 'title': title}, source)


def test_manual_custom_shelf_survives_sync_and_can_be_filtered_on_another_client(client, store):
    seed(store, '111111111')
    result = client.patch('/api/videos/111111111', json={'collection_group': '  运镜参考  '})
    assert result.status_code == 200
    assert result.json()['collection_group_label'] == '运镜参考'
    assert result.json()['collection_group_manual']
    # A subsequent automatic metadata update must preserve the user's shelf.
    services.ingest(store, {'id': '111111111', 'title': 'Codex工作流教程'}, 'favorites')
    listed = client.get('/api/videos', params={'scope': 'personal', 'collection_group': '运镜参考'}).json()
    assert [r['id'] for r in listed['items']] == ['111111111']
    assert client.get('/api/collection-groups').json()['groups']['运镜参考'] == '运镜参考'
    assert client.get('/api/videos', params={'scope': 'personal', 'collection_unconfirmed': True}).json()['total'] == 0
    reset = client.patch('/api/videos/111111111', json={'collection_group': ''}).json()
    assert not reset['collection_group_manual'] and reset['collection_group'] == 'ai_tutorial'
    assert client.get('/api/videos', params={'scope': 'personal', 'collection_unconfirmed': True}).json()['total'] == 1


def test_batch_confirm_is_atomic_and_does_not_change_other_labels_or_sources(client, store):
    for identity in ['111111111', '222222222']:
        seed(store, identity)
    seed(store, '333333333', source='search')
    before = store.rows('SELECT id,category,is_ai,manual_classification,notes,content_tracks FROM videos ORDER BY id')
    result = client.post('/api/videos/batch', json={'ids': ['111111111', '333333333'], 'changes': {'collection_group': '娱乐'}})
    assert result.status_code == 400
    assert store.one('SELECT collection_group FROM videos WHERE id=?', ('111111111',))['collection_group'] == ''
    result = client.post('/api/videos/batch', json={'ids': ['111111111', '222222222'], 'changes': {'collection_group': '娱乐'}})
    assert result.status_code == 200 and result.json()['updated'] == 2
    assert client.get('/api/videos', params={'scope': 'personal', 'collection_group': 'entertainment'}).json()['total'] == 2
    assert client.get('/api/videos', params={'scope': 'personal', 'collection_group': '娱乐'}).json()['total'] == 2
    assert store.rows('SELECT id,category,is_ai,manual_classification,notes,content_tracks FROM videos ORDER BY id') == before
    assert store.one("SELECT COUNT(*) n FROM sources WHERE kind='favorites'")['n'] == 2
    assert store.one('SELECT COUNT(*) n FROM expenses')['n'] == 0


@pytest.mark.parametrize('bad', [None, 123, [], 'x'*41, '教程\n其他', '娱乐\u200b', '全部', '未确认分类'])
def test_manual_shelf_validation_rejects_invalid_values_without_mutation(client, store, bad):
    seed(store, '111111111')
    assert client.patch('/api/videos/111111111', json={'collection_group': bad}).status_code == 400
    assert store.one('SELECT collection_group FROM videos')['collection_group'] == ''


def test_custom_categories_and_unconfirmed_filter_are_personal_only(client, store):
    seed(store, '111111111', source='search')
    assert client.patch('/api/videos/111111111', json={'collection_group': '自填分类'}).status_code == 400
    assert client.get('/api/videos', params={'scope': 'recommendations', 'collection_group': '娱乐'}).status_code == 400
    assert client.get('/api/videos', params={'scope': 'recommendations', 'collection_unconfirmed': True}).status_code == 400
