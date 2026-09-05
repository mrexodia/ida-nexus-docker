const filter = document.querySelector('#artifact-filter');
const folders = [...document.querySelectorAll('.file-folder')];
let savedExpansion = null;

filter.addEventListener('input', () => {
  const query = filter.value.trim().toLowerCase();
  if (query && savedExpansion === null) savedExpansion = new Map(folders.map(folder => [folder, folder.open]));
  let visible = 0;
  for (const file of document.querySelectorAll('.file-node')) {
    file.hidden = !file.dataset.search.includes(query);
    if (!file.hidden) visible++;
  }
  for (const folder of folders) {
    const hasMatch = [...folder.querySelectorAll('.file-node')].some(file => !file.hidden);
    folder.parentElement.hidden = !hasMatch;
    if (query && hasMatch) folder.open = true;
    else if (!query && savedExpansion) folder.open = savedExpansion.get(folder);
  }
  if (!query) savedExpansion = null;
  document.querySelector('#no-artifacts').hidden = visible !== 0;
});
