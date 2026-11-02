function togglePassword(){const i=document.getElementById('password');i.type=i.type==='password'?'text':'password'}
function toggleSidebar(){document.getElementById('admin-sidebar').classList.toggle('open')}
function toggleOwnerMenu(button){const menu=button.nextElementSibling;document.querySelectorAll('.custom-select-menu.open').forEach(function(item){if(item!==menu)item.classList.remove('open')});menu.classList.toggle('open')}
function chooseOwner(button,id,label){const root=button.closest('.custom-select');root.querySelector('.custom-select-trigger span').textContent=label;root.closest('form').querySelector('input[name="owner_id"]').value=id;root.querySelector('.custom-select-menu').classList.remove('open')}
document.addEventListener('click',function(e){if(!e.target.closest('.custom-select'))document.querySelectorAll('.custom-select-menu.open').forEach(function(menu){menu.classList.remove('open')})});
