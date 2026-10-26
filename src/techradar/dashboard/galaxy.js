/* Atlas UI. Geometry and orbit controls live in GalaxyScene. */
(() => {
  'use strict';
  const DATA = __DATA__;
  const $ = id => document.getElementById(id);
  const canvas = $('gxcanvas'), panel = $('gxpanel');
  const wrap = document.querySelector('.gxwrap');
  const galaxies = DATA.galaxies;
  const sourceNames = {github:'GitHub', arxiv:'arXiv', rss:'行业动态', web:'网页', document:'文档'};
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  let focus = null, selected = null, query = '', pageSize = 18, panelHidden = false;
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function announce(value) { $('gxannounce').textContent = value; }
  function safeUrl(value) { try { const u = new URL(value); return ['http:', 'https:'].includes(u.protocol) ? u.href : ''; } catch { return ''; } }
  const scene = new GalaxyScene({canvas, data:DATA, tooltip:document.querySelector('.gxtooltip'), reducedMotion,
    matchNode:n => matches(n),
    onPick:hit => hit.node ? selectItem(hit.node) : enterGalaxy(hit.galaxy),
    onState:state => {
      if (state.zoom !== undefined) $('gxscale').textContent = Math.round(state.zoom * 100) + '%';
      if (state.level) $('gxlevel').textContent = state.level;
      $('gxorbit').setAttribute('aria-pressed', String(state.autoOrbit));
    }});
  const byCat = scene.byCat, byItem = scene.byItem;
  function draw() { scene.query = query; scene.draw(); }
  function scoreText(n) { return n.scored === false ? '未评分' : Math.round(n.score); }
  function galaxyButton(g) {
    return `<button class="gxgalaxy" data-galaxy="${esc(g.category)}" style="--hue:${g.hue}" aria-label="进入${esc(g.label)}星系"><i></i><span class="gxname">${esc(g.label)}</span><span class="gxcount">${g.count || g.tagged_count + ' 关联'}</span><span class="gxarrow">↗</span></button>`;
  }
  function itemButton(n) {
    return `<button class="gxitem" data-item="${esc(n.id)}" aria-label="查看情报：${esc(n.label)}"><span><span class="gxitemtitle">${esc(n.label)}</span><span class="gxitemmeta">${esc(sourceNames[n.source_type] || n.source_type || '技术情报')} · ${esc(n.galaxy.label)}</span></span><span class="gxitemscore">${scoreText(n)}</span></button>`;
  }
  function matches(n) {
    return n.label.toLowerCase().includes(query) || n.summary.toLowerCase().includes(query) || n.galaxy.label.toLowerCase().includes(query) || n.categories.some(c => byCat.get(c)?.label.toLowerCase().includes(query));
  }
  function renderPanel() {
    wrap.classList.toggle('has-focus', !!focus); wrap.classList.toggle('has-search', !!query);
    wrap.classList.toggle('panel-collapsed', panelHidden);
    $('gxdetails').setAttribute('aria-expanded', String(!panelHidden));
    $('gxdetails').textContent = panelHidden ? '展开详情' : '收起详情';
    $('gxcrumbtext').textContent = focus ? ' / ' + focus.label + (selected ? ' / 情报' : '') : ' / 技术全景';
    $('gxscenehint').textContent = selected ? '已展开跨方向关联' : focus ? '高分靠近中心 · 拖拽旋转 · 点击星点阅读' : '拖拽旋转，点击星系深入';
    if (query) {
      const groups = galaxies.filter(g => g.label.toLowerCase().includes(query));
      const items = [...byItem.values()].filter(matches);
      panel.innerHTML = `<p class="gxoverline">SEARCH RESULTS</p><h2>搜索星图</h2><p class="gxintro">${groups.length} 个方向 · ${items.length} 条情报</p>` + groups.map(galaxyButton).join('') + items.slice(0, pageSize).map(itemButton).join('') + (items.length > pageSize ? '<button class="gxquiet" data-more>显示更多结果</button>' : '') + (!groups.length && !items.length ? '<p class="gxsummary">没有找到匹配结果。试试技术名、仓库名或方向名称。</p>' : '');
      announce(`找到 ${groups.length} 个方向，${items.length} 条情报`);
    } else if (selected) {
      const n = selected, url = safeUrl(n.url);
      panel.innerHTML = `<button class="gxpanelback" data-back>← 返回${esc(focus.label)}</button><p class="gxoverline">INTELLIGENCE / ${esc(sourceNames[n.source_type] || '技术情报')}</p><h2 style="font-size:15px">${esc(n.label)}</h2><div class="gxscore">${scoreText(n)}<small>${n.scored === false ? '' : '相关度 / 100'}</small></div><p class="gxsummary">${esc(n.summary || '这条情报暂无摘要，可打开原文了解详细内容。')}</p><div class="gxsection"><span>所属方向</span><span>${n.categories.length ? n.categories.length + ' TAGS' : '待分类'}</span></div><div class="gxtags">` + n.categories.map(c => {
        const g = byCat.get(c); return g ? `<button class="gxtag" data-galaxy="${esc(c)}" style="--hue:${g.hue}"><i></i>${esc(g.label)}${c === n.category ? ' · 主' : ' ↗'}</button>` : '';
      }).join('') + `</div><p class="gxsmall">${n.categories.length ? '星点位于主方向；其他标签通过航线连接对应星系。' : '尚无有效方向标签，暂存于待分类星系，仍可阅读和搜索。'}</p>${url ? `<a class="gxlink" href="${esc(url)}" target="_blank" rel="noopener noreferrer">阅读原文 ↗</a>` : '<p class="gxsmall">暂无可用原文链接</p>'}`;
    } else if (focus) {
      const related = DATA.bridges.filter(b => b.source === focus.category || b.target === focus.category);
      const taggedItems = [...byItem.values()].filter(n => n.category !== focus.category && n.categories.includes(focus.category));
      panel.innerHTML = `<button class="gxpanelback" data-home>← 返回全部星系</button><p class="gxoverline">GALAXY / ${String(galaxies.indexOf(focus) + 1).padStart(2,'0')}</p><h2>${esc(focus.label)}</h2><div class="gxpanelcounts"><div><b>${focus.count}</b><small>主归属情报</small></div><div><b>${related.length}</b><small>关联星系</small></div></div><details class="gxconnections"><summary>跨方向航线 · ${related.length}</summary>` + (related.length ? related.map(b => {
        const g = byCat.get(b.source === focus.category ? b.target : b.source);
        return `<button class="gxrelated" data-galaxy="${esc(g.category)}">${esc(g.label)} ↗<span>${b.weight} 条交叉</span></button>`;
      }).join('') : '<p class="gxsmall">这个方向暂时没有跨方向情报。</p>') + `</details><div class="gxsection"><span>按相关度阅读</span><span>${focus.items.length} ITEMS</span></div>` + focus.items.slice(0, pageSize).map(itemButton).join('') + (focus.items.length > pageSize ? '<button class="gxquiet" data-more>显示更多情报</button>' : '') + (taggedItems.length ? `<div class="gxsection"><span>来自其他星系的关联情报</span><span>${taggedItems.length}</span></div>` + taggedItems.slice(0, pageSize).map(itemButton).join('') + (taggedItems.length > pageSize ? '<button class="gxquiet" data-more>显示更多关联情报</button>' : '') : '') + '<p class="gxreading">评分越高，星点越大、越靠近中心。<br>主归属按标签权重，缺少权重时取分类首标签。</p>';
    } else {
      panel.innerHTML = `<p class="gxoverline">EXPLORE THE ATLAS</p><h2>发现技术之间的引力</h2><p class="gxintro">当前展示 ${DATA.stats.items} 条情报。<br>每颗亮星对应一条，点击星系展开。</p><div class="gxsection"><span>技术星系</span><span>${galaxies.length} GALAXIES</span></div>` + galaxies.map(galaxyButton).join('') + '<p class="gxreading">每条情报只有一个主归属。<br>航线来自同一情报的多个方向标签，<br>表示共现关系。</p>';
    }
    document.querySelector('.gxinfo').scrollTop = 0;
  }

  function enterGalaxy(g, smooth = true) {
    focus = g; selected = null; query = ''; $('gxsearch').value = ''; pageSize = 18; panelHidden = false;
    renderPanel(); scene.setFocus(g, null, smooth); announce(`已进入${g.label}，${g.count} 条主归属情报`);
  }
  function selectItem(n) {
    const changed = focus !== n.galaxy;
    focus = n.galaxy; selected = n; query = ''; $('gxsearch').value = ''; panelHidden = false;
    renderPanel();
    if (changed || scene.zoomLevel() < 1.6) scene.setFocus(focus, n);
    else scene.setSelected(n);
    announce(`已选择 ${n.label}`);
  }
  function home() {
    focus = selected = null; query = ''; $('gxsearch').value = ''; pageSize = 18; panelHidden = false;
    renderPanel(); scene.reset(); announce('已返回 3D 技术全景');
  }
  panel.addEventListener('click', e => {
    const button = e.target.closest('button'); if (!button) return;
    if (button.dataset.galaxy) enterGalaxy(byCat.get(button.dataset.galaxy));
    else if (button.dataset.item) selectItem(byItem.get(button.dataset.item));
    else if (button.hasAttribute('data-home')) home();
    else if (button.hasAttribute('data-back')) { selected = null; renderPanel(); scene.setSelected(null); }
    else if (button.hasAttribute('data-more')) {
      const scroll = document.querySelector('.gxinfo').scrollTop;
      pageSize += 30; renderPanel(); document.querySelector('.gxinfo').scrollTop = scroll;
    }
  });
  $('gxsearch').addEventListener('input', e => {
    scene.setAutoOrbit(false);
    query = e.target.value.trim().toLowerCase(); pageSize = 18; panelHidden = false; renderPanel(); draw();
  });
  $('gxsearch').addEventListener('keydown', e => {
    if (e.key === 'Enter' && query) {
      const g = galaxies.find(g => g.label.toLowerCase().includes(query));
      const n = [...byItem.values()].find(matches);
      if (g) enterGalaxy(g); else if (n) selectItem(n);
    }
  });
  $('gxrelations').onclick = () => {
    scene.showRelations = !scene.showRelations;
    $('gxrelations').setAttribute('aria-pressed', String(scene.showRelations)); draw();
  };
  $('gxhome').onclick = $('gxfit').onclick = home;
  $('gxdetails').onclick = () => { panelHidden = !panelHidden; renderPanel(); };
  $('gxin').onclick = () => scene.zoom(1.3);
  $('gxout').onclick = () => scene.zoom(1 / 1.3);
  $('gxpan').onclick = () => {
    scene.setAutoOrbit(false);
    scene.mode = scene.mode === 'rotate' ? 'pan' : 'rotate';
    $('gxpan').setAttribute('aria-pressed', String(scene.mode === 'pan'));
    canvas.style.cursor = scene.mode === 'pan' ? 'move' : 'grab';
    announce(scene.mode === 'pan' ? '已切换为平移，拖拽移动视角' : '已切换为旋转，拖拽环绕星系');
  };
  $('gxorbit').disabled = reducedMotion;
  if (reducedMotion) $('gxorbit').title = '系统已启用减少动态效果';
  $('gxorbit').onclick = () => scene.setAutoOrbit(!scene.autoOrbit);
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') { home(); $('gxsearch').blur(); }
    if (e.target instanceof HTMLInputElement) return;
    if (e.key === '/') { e.preventDefault(); $('gxsearch').focus(); }
    if (e.key === '+' || e.key === '=') { e.preventDefault(); scene.zoom(1.3); }
    if (e.key === '-') { e.preventDefault(); scene.zoom(1 / 1.3); }
    if (e.target === canvas && ['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(e.key)) {
      e.preventDefault(); scene.setAutoOrbit(false);
      if (e.shiftKey) {
        scene.camera.panX += e.key === 'ArrowLeft' ? 35 : e.key === 'ArrowRight' ? -35 : 0;
        scene.camera.panY += e.key === 'ArrowUp' ? 35 : e.key === 'ArrowDown' ? -35 : 0; draw();
      } else scene.rotate(e.key === 'ArrowLeft' ? -.09 : e.key === 'ArrowRight' ? .09 : 0,
        e.key === 'ArrowUp' ? -.07 : e.key === 'ArrowDown' ? .07 : 0);
    }
  });
  const s = DATA.stats;
  $('gxstats').innerHTML = `<strong>${s.galaxies}</strong> 星系 &nbsp; / &nbsp; 当前 <strong>${s.items}</strong>${s.library_items !== undefined ? ' / 全库 <strong>' + s.library_items + '</strong>' : ''} 条 &nbsp; / &nbsp; <strong>${s.cross_items || 0}</strong> 条跨方向`;
  document.querySelector('.gxempty').hidden = !!galaxies.length;
  renderPanel();
})();
