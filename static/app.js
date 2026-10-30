// Общие небольшие улучшения интерфейса. Основная логика экранов находится в шаблонах.
document.querySelectorAll('a.secondary-button').forEach(link => link.addEventListener('click', () => link.classList.add('loading')));
