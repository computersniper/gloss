"""Research directions: filing projects under a category and the direction's hub project."""


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def test_file_projects_under_a_direction_and_open_its_hub(client, paper_id):
    paper_project = ok(client.get(f'/api/papers/{paper_id}/project'))
    research = ok(client.post('/api/agent/projects', json={'name': 'Side study'}))

    filed = ok(client.patch(f"/api/agent/projects/{paper_project['id']}", json={'category': '  LLM   reasoning '}))
    assert filed['category'] == 'LLM reasoning'
    ok(client.patch(f"/api/agent/projects/{research['id']}", json={'category': 'LLM reasoning'}))

    hub = ok(client.post('/api/agent/categories/hub', json={'name': 'LLM reasoning'}))
    assert hub['hub_category'] == 'LLM reasoning' and hub['kind'] == 'research'
    assert paper_id in hub['paper_ids']
    # asking again returns the same project
    assert ok(client.post('/api/agent/categories/hub', json={'name': 'LLM reasoning'}))['id'] == hub['id']

    # papers added to a filed project flow into the hub
    second = client.post('/api/papers/upload', files={'file': ('n.md', b'# Notes on chain of thought\n\nSome text.', 'text/markdown')})
    second_id = ok(second)['id']
    ok(client.post(f"/api/agent/projects/{research['id']}/papers", json={'paper_id': second_id}))
    assert second_id in ok(client.get(f"/api/agent/projects/{hub['id']}"))['paper_ids']

    # the hub itself cannot be refiled; renaming moves everything
    assert client.patch(f"/api/agent/projects/{hub['id']}", json={'category': 'Other'}).status_code == 400
    assert ok(client.post('/api/agent/categories/rename', json={'name': 'LLM reasoning', 'new_name': 'Reasoning'}))['changed'] == 3
    listed = {p['id']: p for p in ok(client.get('/api/agent/projects'))}
    assert listed[research['id']]['category'] == 'Reasoning'
    assert listed[hub['id']]['hub_category'] == 'Reasoning' and listed[hub['id']]['name'] == 'Reasoning'

    # unfiling
    assert ok(client.patch(f"/api/agent/projects/{research['id']}", json={'category': ''}))['category'] == ''
    assert client.post('/api/agent/categories/hub', json={'name': '   '}).status_code in (400, 422)
