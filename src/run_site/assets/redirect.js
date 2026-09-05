const link = document.querySelector('#redirect');
if (link) location.replace(link.href + location.hash);
