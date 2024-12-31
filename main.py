import requests
import time
import aiohttp
import asyncio

def load_config(file_path):
    config = {}
    with open(file_path, 'r') as file:
        for line in file.readlines():
            key, value = line.strip().split('=')
            config[key] = value
    return config

config = load_config("config.txt")

GITHUB_TOKEN = config.get("GITHUB_TOKEN")
GITFLIC_TOKEN = config.get("GITFLIC_TOKEN")
GITFLIC_URL = config.get("GITFLIC_URL", "https://api.gitflic.ru")  # URL API по умолчанию - https://api.gitflic.ru
GITFLIC_RATE_LIMIT = int(config.get("GITFLIC_RATE_LIMIT", 0))  # Ограничение по умолчанию отключено
TRANSFER_TYPE = config.get("TRANSFER_TYPE", "all").lower()  # По умолчанию переносим всё


project_id_cache = {}
user_cache = {}

class RateLimiter:
    def __init__(self, max_requests, period):
        """
        Инициализация лимитера.
        :param max_requests: Максимальное количество запросов. Если 0, лимитер не используется.
        :param period: Период в секундах (1 час = 3600 секунд).
        """
        self.max_requests = max_requests
        self.period = period
        self.requests = []
    
    def wait_if_needed(self):
        """
        Ждёт, если лимит запросов превышен. Если max_requests=0, не ограничивает.
        """
        
        now = time.time()
        # Удаляем старые запросы
        self.requests = [req_time for req_time in self.requests if now - req_time < self.period]
        if len(self.requests) >= self.max_requests:
            wait_time = self.period - (now - self.requests[0])
            print(f"Достигнут лимит запросов. Ожидание {wait_time:.2f} секунд...")
            time.sleep(wait_time)
        self.requests.append(time.time())


rate_limiter = RateLimiter(max_requests=GITFLIC_RATE_LIMIT, period=3600)  # 1 час = 3600 секунд

async def fetch(session, url, headers, method='GET', payload=None):
    """
    Асинхронный HTTP-запрос с aiohttp.
    """
    if GITFLIC_RATE_LIMIT > 0:
        rate_limiter.wait_if_needed() 

    try:
        async with session.request(method, url, headers=headers, json=payload) as response:
            if response.status in [200, 201]:
                return await response.json()
            elif response.status in [429]:
                print(f"Ошибка {response.status}: {await response.text()}. Ждём 5 секунд...")
                await asyncio.sleep(5)
            else:
                print(f"Ошибка {response.status}: {await response.text()}")
    except Exception as e:
        print(f"Ошибка при выполнении запроса: {e}")
    return None

# Маппинг статусов проблем
def get_gitflic_status(github_status, github_state_reason=None):
    if github_status == "open":
        return "OPEN"
    elif github_status == "closed":
        if github_state_reason == "not_planned":
            return "CLOSED"
        elif github_state_reason == "completed":
            return "COMPLETED"
    return "OPEN"  # По умолчанию открытый статус

# Получение информации о пользователе из GitFlic по логину
def get_gitflic_user_by_alias(username):
    """
    Получает ID пользователя GitFlic по имени пользователя GitHub.
    Кэширует результат для уменьшения количества запросов.
    """
    url = f"{GITFLIC_URL}/user/{username}"
    headers = {
        "Authorization": f"token {GITFLIC_TOKEN}",
        "Content-Type": "application/json"
    }
    
    response = requests.get(url, headers=headers)

    if response.status_code == 200:
        return response.json()['id']
    else:
        print(f"Пользователь {username} не найден в GitFlic и не будет указан в качестве ответственного")
        return None
    
# Получение информации о проекте из GitFlic
def get_gitflic_project_id(gitflic_repo):
    if gitflic_repo in project_id_cache:
        return project_id_cache[gitflic_repo]
    url = f"{GITFLIC_URL}/project/{gitflic_repo}"
    headers = { 
        "Authorization": f"token {GITFLIC_TOKEN}",
        "Content-Type": "application/json"
    }
    response = requests.get(url, headers=headers)
    if response.status_code == 200:
        return response.json().get('id') # Возвращаем ID проекта 
    else:
        print(f"Ошибка: не удалось получить проект {gitflic_repo}. {response.status_code}: {response.text}")
        return None 

async def check_branch_exists(session, gitflic_repo, branch_name):
    """
    Проверяет существование ветки в репозитории GitFlic.
    """
    url = f"{GITFLIC_URL}/project/{gitflic_repo}/branch"
    headers = {
        "Authorization": f"token {GITFLIC_TOKEN}",
        "Content-Type": "application/json"
    }

    response = await fetch(session, url, headers)
    if not response:
        print(f"Ошибка: Не удалось получить список веток для проекта {gitflic_repo}.")
        return False
    
    branches = response.get('_embedded', {}).get('branchList', [])
    branch_names = [branch['name'] for branch in branches]
    
    return branch_name in branch_names


# Получение issues из GitHub
async def get_github_issues(session, github_api_url):
    headers = {"Authorization": f"token {GITHUB_TOKEN}"}
    issues = []
    page = 1
    per_page = 100  # Максимальное значение для GitHub API

    while True:
        # Добавляем параметры пагинации
        paginated_url = f"{github_api_url}/issues?state=all&direction=asc&per_page={per_page}&page={page}"

        async with session.get(paginated_url, headers=headers) as response:
            if response.status != 200:
                print(f"Ошибка при получении проблем с GitHub: {response.status}")
                print(await response.text)  # Выводим подробности ошибки
                break

            page_issues = await response.json()
            print(f"Количество проблем на странице {page}: {len(page_issues)}")  # Отладочный вывод

            # Исключаем пул-реквесты
            filtered_issues = [issue for issue in page_issues if 'pull_request' not in issue]

            if not filtered_issues:  # Если задач больше нет, выходим из цикла
                break

            issues.extend(filtered_issues)
            page += 1  # Переход к следующей странице

    total_issues = len(issues)
    print(f"Извлечено {total_issues} проблем(ы).")
    
    return issues, total_issues

# Получение MR из GitHub
async def get_github_pull_requests(session, github_api_url, repo_name):
    headers = {"Authorization": f"token {GITHUB_TOKEN}"}
    pull_requests = []
    page = 1
    per_page = 100

    while True:
        paginated_url = f"{github_api_url}/pulls?state=all&direction=asc&per_page={per_page}&page={page}"

        async with session.get(paginated_url, headers=headers) as response:
            if response.status != 200:
                print(f"Ошибка при получении pull requests с GitHub: {response.status}")
                print(await response.text)
                break

            page_pull_requests = await response.json()

            if not page_pull_requests:
                break
            print(f"Количество MR на странице {page}: {len(page_pull_requests)}")  # Отладочный вывод   

            # Фильтруем ветки которые относятся к текущему репозиторию
            filtered_pulls = [
                pr for pr in page_pull_requests
                if pr.get('head', {}).get('repo') and pr.get('base', {}).get('repo') and
                    pr['head']['repo']['full_name'] == repo_name and pr['base']['repo']['full_name'] == repo_name
            ]

            pull_requests.extend(filtered_pulls)
            page += 1

    total_prs = len(pull_requests)
    print(f"Извлечено {total_prs} pull requests.")
    return pull_requests, total_prs

# Создание issue в GitFlic
async def create_gitflic_issue(session, gitflic_api_url, title, description, status, assigned_users, labels):
    """
    Асинхронное создание issue в GitFlic.
    """
    if not description:
        print(f"Описание для проблемы '{title}' пустое. Заполняем описание заголовком...")
        description = title

    headers = {
        "Authorization": f"token {GITFLIC_TOKEN}",
        "Content-Type": "application/json"
    }

    payload = {
        "title": title,
        "description": description,
        "status": {"id": status},
        "assignedUsers": [{"id": user} for user in assigned_users],
        "labels": [{"id": label} for label in labels]
    }

    response = await fetch(session, gitflic_api_url, headers, method='POST', payload=payload)
    if response:
        print(f"Issue '{title}' успешно создана в GitFlic.")

async def create_gitflic_mr(session, gitflic_api_url, title, description, source_branch, target_branch, project_id, gitflic_repo):
    """
    Асинхронное создание MR в GitFlic.
    """
    # Проверяем существование веток
    source_branch_exists = await check_branch_exists(session, gitflic_repo, source_branch)
    target_branch_exists = await check_branch_exists(session, gitflic_repo, target_branch)

    if not source_branch_exists:
        print(f"Пропуск MR '{title}': исходная ветка '{source_branch}' отсутствует в проекте.")
        return
    if not target_branch_exists:
        print(f"Пропуск MR '{title}': целевая ветка '{target_branch}' отсутствует в проекте.")
        return

    headers = {
        "Authorization": f"token {GITFLIC_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "title": title,
        "description": description or "No description provided",
        "sourceBranch": {"id": source_branch},
        "targetBranch": {"id": target_branch},
        "sourceProject": {"id": project_id},
        "targetProject": {"id": project_id},
        "squashCommit": True,
        "removeSourceBranch": True
    }


    response = await fetch(session, gitflic_api_url, headers, method='POST', payload=payload)
    if response:
        print(f"MR '{title}' успешно создан в GitFlic.")


async def process_repo(session, github_repo, gitflic_repo):
    """
    Асинхронная обработка репозитория: перенос issues и merge requests.
    """
    github_api_url = f"https://api.github.com/repos/{github_repo}"
    gitflic_api_url_issues = f"{GITFLIC_URL}/project/{gitflic_repo}/issue"
    gitflic_api_url_mrs = f"{GITFLIC_URL}/project/{gitflic_repo}/merge-request"

    if TRANSFER_TYPE not in ["issues", "mr", "all"]:
        raise ValueError("Неверное значение TRANSFER_TYPE. Допустимые значения: 'issues', 'mr', 'all'.")

    # Перенос задач (issues), если указано
    if TRANSFER_TYPE in ["issues", "all"]:
        print(f"Извлечение проблем из {github_repo}...")
        github_issues, _ = await get_github_issues(session, github_api_url)
        for issue in github_issues:
            title = issue['title']
            description = issue['body']
            github_status = issue['state']  # 'open' или 'closed'
            github_state_reason = issue.get('state_reason')  # может быть 'not_planned' или 'completed'
            status = get_gitflic_status(github_status, github_state_reason)
            assigned_users = []

            for assignee in issue.get('assignees', []):
                github_username = assignee['login']
                user_id = get_gitflic_user_by_alias(github_username)
                if user_id:
                    assigned_users.append(user_id)

            await create_gitflic_issue(session, gitflic_api_url_issues, title, description, status, assigned_users, [])
    
    # Перенос запросов на слияние (Merge Requests), если указано
    if TRANSFER_TYPE in ["mr", "all"]:
        print(f"Извлечение ЗнС из {github_repo}...")
        github_pulls, _ = await get_github_pull_requests(session, github_api_url, github_repo)
        if github_pulls:  # Если есть ЗнС, получаем project_id
            project_id = get_gitflic_project_id(gitflic_repo)
            if not project_id:
                print(f"Не удалось получить ID проекта для {gitflic_repo}, пропуск ЗнС...")
                return

            for pull in github_pulls:
                title = pull['title']
                description = pull.get('body', title)
                source_branch = pull['head']['ref']
                target_branch = pull['base']['ref']
                await create_gitflic_mr(session, gitflic_api_url_mrs, title, description, source_branch, target_branch, project_id, gitflic_repo)

async def main():
    async with aiohttp.ClientSession() as session:
        with open("repos.txt", 'r') as file:
            lines = file.readlines()

        tasks = []
        for line in lines:
            github_repo, gitflic_repo = line.strip().split(';')
            tasks.append(process_repo(session, github_repo, gitflic_repo))

        await asyncio.gather(*tasks)

if __name__ == "__main__":
    asyncio.run(main())
