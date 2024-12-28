import requests
import time
import json

def load_tokens(file_path):
    tokens = {}
    with open(file_path, 'r') as file:
        for line in file.readlines():
            key, value = line.strip().split('=')
            tokens[key] = value
    return tokens

tokens = load_tokens("token.txt")

# Конфигурация
GITHUB_TOKEN = tokens.get("GITHUB_TOKEN")
GITFLIC_TOKEN = tokens.get("GITFLIC_TOKEN")

# Маппинг статусов GitHub в GitFlic
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
def get_gitflic_user_by_alias(username, issue_title=None):
    url = f"https://api.gitflic.ru/user/{username}"
    headers = {
        "Authorization": f"token {GITFLIC_TOKEN}",
        "Content-Type": "application/json"
    }
    
    response = requests.get(url, headers=headers)

    if response.status_code == 200:
        return response.json()['id']
    else:
        print(f"Пользователь {username} не найден в GitFlic и не будет указан в качестве ответственного у проблемы {issue_title}.")
        return None

# Получение issues из GitHub
def get_github_issues(github_api_url):
    headers = {"Authorization": f"token {GITHUB_TOKEN}"}
    issues = []
    page = 1
    per_page = 100  # Максимальное значение для GitHub API

    while True:
        # Добавляем параметры пагинации
        paginated_url = f"{github_api_url}&per_page={per_page}&page={page}"
        response = requests.get(paginated_url, headers=headers)

        if response.status_code != 200:
            print(f"Ошибка при получении issues с GitHub: {response.status_code}")
            print(response.text)  # Выводим подробности ошибки
            break

        page_issues = response.json()
        if not page_issues:  # Если задач больше нет, выходим из цикла
            break

        issues.extend(page_issues)
        page += 1  # Переход к следующей странице

    total_issues = (len(issues))
    print(f"Извлечено {total_issues} задач.")
    
    return issues, total_issues

# Создание issue в GitFlic
def create_gitflic_issue(gitflic_api_url, title, description, status, assigned_users, labels, total_issues, retries=3, delay=8):
    if not description:
        print(f"Описание для проблемы '{title}' пустое. Заполняем описание заголовком...")
        description = title

    url = gitflic_api_url
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

    for attempt in range(retries):
        response = requests.post(url, headers=headers, data=json.dumps(payload))
        
        if response.status_code == 200:
            print(f"Issue '{title}' успешно создана в GitFlic.")
            if total_issues > 500:
                print(f"Задач слишком много, ждем несколько секунд после создания задачи...")
                time.sleep(delay)
            return  # Успешно создали, выходим из функции
        
        elif response.status_code == 403:
            print(f"Ошибка 403. Попытка {attempt + 1} из {retries}. Ждём несколько секунд...")
            time.sleep(delay)
            break
        elif response.status_code == 422:
            print(f"Ошибка 422: Невалидные данные для issue '{title}'. Пропускаем...")
            return 
        elif response.status_code == 429:
            print(f"Ошибка 429. Попытка {attempt + 1} из {retries}. Ждём несколько секунд...")
            time.sleep(delay)
            break
        else:
            print(f"Ошибка при переносе issue '{title}' в GitFlic: {response.status_code}, {response.text}")
            break


# Основная функция для импорта issues из GitHub в GitFlic
def import_issues_from_github_to_gitflic(github_api_url, gitflic_api_url):
    github_issues, total_issues = get_github_issues(github_api_url)

    for issue in github_issues:
        title = issue['title']
        description = issue['body']

        # Извлекаем статус и reason из GitHub
        github_status = issue['state']  # 'open' или 'closed'
        github_state_reason = issue.get('state_reason')  # может быть 'not_planned' или 'completed'
        status = get_gitflic_status(github_status, github_state_reason)
        assigned_users = []

        for assignee in issue.get('assignees', []):
            github_username = assignee['login']
            user_id = get_gitflic_user_by_alias(github_username, issue_title=title)
            if user_id:
                assigned_users.append(user_id)
        labels = []

        create_gitflic_issue(gitflic_api_url, title, description, status, assigned_users, labels, total_issues)

def process_repos_file(filename):
    with open(filename, 'r') as file:
        lines = file.readlines()

    for line in lines:
        # Разделяем строку на данные о репозиториях
        github_repo, gitflic_repo = line.strip().split(';')

        # Формируем API URL для GitHub и GitFlic
        github_api_url = f"https://api.github.com/repos/{github_repo}/issues?state=all&direction=asc"
        gitflic_api_url = f"https://api.gitflic.ru/project/{gitflic_repo}/issue"
        
        print(f"Извлечение проблем из {github_repo}...")
        import_issues_from_github_to_gitflic(github_api_url, gitflic_api_url)

# Пример вызова функции
if __name__ == "__main__":
    process_repos_file("repos.txt")
