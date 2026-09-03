"""Composant de navigation partagé par toutes les pages générées."""

from html import escape


NAVIGATION_CSS = """
.title{position:relative;background:#18295c;color:#fff;padding:7px 48px;text-align:center;font-size:clamp(14px,1.7vw,22px);font-weight:800;line-height:1.1}
.menu-button{position:absolute;left:4px;top:0;bottom:0;margin:auto;width:42px;height:32px;padding:0;border:0;background:transparent;color:#fff;font-size:0;cursor:pointer;appearance:none;-webkit-appearance:none}
.menu-button::before{content:"";position:absolute;left:50%;top:50%;width:25px;height:3px;transform:translate(-50%,-50%);border-radius:2px;background:#fff;box-shadow:0 -8px #fff,0 8px #fff}
.share-button{position:absolute;right:5px;top:0;bottom:0;margin:auto;width:40px;height:32px;padding:4px;border:0;background:transparent;color:#fff;cursor:pointer}
.share-button svg{display:block;width:24px;height:24px;margin:auto;fill:none;stroke:currentColor;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}
.title-label{display:inline-flex;align-items:center;justify-content:center;gap:5px}.title-link{color:inherit;text-decoration:none}.settings-button{width:36px;height:32px;padding:0;border:0;background:transparent;color:#fff;font-size:29px;font-weight:700;line-height:1;cursor:pointer;text-shadow:0 1px 2px #0005}
.route-menu{position:absolute;z-index:2000;left:8px;top:38px;min-width:220px;background:#fff;border-radius:10px;padding:6px;box-shadow:0 5px 20px #0005;text-align:left}
.route-menu[hidden]{display:none}.route-menu a{display:block;padding:9px 11px;border-radius:7px;color:#17234d;text-decoration:none;font-size:14px;font-weight:650}.route-menu a:hover{background:#edf1fa}
"""

NAVIGATION_SCRIPT = """
const menuButton=document.querySelector('#menu-button'),routeMenu=document.querySelector('#route-menu');
menuButton.onclick=event=>{event.stopPropagation();routeMenu.hidden=!routeMenu.hidden};
document.addEventListener('click',event=>{if(!routeMenu.contains(event.target)&&event.target!==menuButton)routeMenu.hidden=true});
document.addEventListener('keydown',event=>{if(event.key==='Escape')routeMenu.hidden=true});
const shareButton=document.querySelector('#share-button');
shareButton.onclick=async()=>{const shareData={title:document.title,url:location.href};try{if(navigator.share)await navigator.share(shareData);else{await navigator.clipboard.writeText(location.href);shareButton.title='Lien copié';setTimeout(()=>shareButton.title='Partager',1600)}}catch(error){if(error.name!=='AbortError')console.warn('Partage impossible',error)}};
"""


def render_route_links(routes, prefix=""):
    return "".join(
        f'<a href="{escape(prefix + slug)}/">{escape(title)}</a>'
        for slug, title in routes
    )


def render_navigation(title, routes, home_href, route_prefix="", settings=False,
                      title_href=None):
    links = render_route_links(routes, route_prefix)
    settings_button = (
        '<button id="settings-button" class="settings-button" '
        'aria-label="Paramètres du voyage" title="Paramètres du voyage">⚙</button>'
        if settings else ""
    )
    title_html = (f'<a class="title-link" href="{escape(title_href)}">{escape(title)}</a>'
                  if title_href else escape(title))
    return (
        '<header class="title">'
        '<button id="menu-button" class="menu-button" aria-label="Ouvrir le menu">☰</button>'
        f'<span class="title-label">{title_html}{settings_button}</span>'
        '<button id="share-button" class="share-button" aria-label="Partager cette page" title="Partager">'
        '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="18" cy="5" r="2.5"/>'
        '<circle cx="6" cy="12" r="2.5"/><circle cx="18" cy="19" r="2.5"/>'
        '<path d="m8.2 10.8 7.6-4.5M8.2 13.2l7.6 4.5"/></svg></button></header>'
        f'<nav id="route-menu" class="route-menu" hidden><a href="{escape(home_href)}">Accueil et aide</a>{links}</nav>'
    )
