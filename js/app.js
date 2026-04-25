/* Chattanooga Events Calendar — app.js */

(function () {
  'use strict';

  let allEvents = [];

  // ── Category colours ────────────────────────────────────────────────────────
  const CATEGORY_COLORS = {
    Music:  '#7c3aed',
    Arts:   '#db2777',
    Sports: '#059669',
    Food:   '#d97706',
    Family: '#2563eb',
    Other:  '#475569',
  };

  function categoryColor(cat) {
    return CATEGORY_COLORS[cat] || CATEGORY_COLORS.Other;
  }

  // ── Helpers ──────────────────────────────────────────────────────────────────
  function formatDate(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    return d.toLocaleDateString('en-US', {
      weekday: 'short', month: 'short', day: 'numeric', year: 'numeric'
    });
  }

  function formatTime(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    // If midnight, don't show a time (all-day events stored at T00:00:00)
    if (d.getHours() === 0 && d.getMinutes() === 0) return '';
    return d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
  }

  function formatDateTime(startIso, endIso) {
    const date = formatDate(startIso);
    const start = formatTime(startIso);
    const end   = formatTime(endIso);
    if (start && end)  return `${date} · ${start} – ${end}`;
    if (start)         return `${date} · ${start}`;
    return date;
  }

  // ── Tab navigation ───────────────────────────────────────────────────────────
  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
      document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
      btn.classList.add('active');
      document.getElementById('tab-' + btn.dataset.tab).classList.add('active');
    });
  });

  // ── Modal ────────────────────────────────────────────────────────────────────
  const modal        = document.getElementById('event-modal');
  const modalContent = document.getElementById('modal-content');
  const modalClose   = document.getElementById('modal-close');

  function openModal(event) {
    const time = formatDateTime(event.start, event.end);
    modalContent.innerHTML = `
      <div class="modal-title">${escHtml(event.title)}</div>
      <div class="modal-meta">
        <span><strong>When:</strong> ${escHtml(time)}</span>
        ${event.venue    ? `<span><strong>Where:</strong> ${escHtml(event.venue)}</span>` : ''}
        ${event.category ? `<span><strong>Category:</strong> ${escHtml(event.category)}</span>` : ''}
        ${event.source   ? `<span><strong>Source:</strong> ${escHtml(event.source)}</span>` : ''}
      </div>
      ${event.description ? `<div class="modal-desc">${escHtml(event.description)}</div>` : ''}
      ${event.url ? `<a class="modal-link" href="${escHtml(event.url)}" target="_blank" rel="noopener">More Info / Tickets</a>` : ''}
    `;
    modal.style.display = 'flex';
  }

  modalClose.addEventListener('click', () => { modal.style.display = 'none'; });
  modal.addEventListener('click', e => { if (e.target === modal) modal.style.display = 'none'; });
  document.addEventListener('keydown', e => { if (e.key === 'Escape') modal.style.display = 'none'; });

  function escHtml(str) {
    return String(str || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  // ── Events List tab ──────────────────────────────────────────────────────────
  const searchInput      = document.getElementById('search-input');
  const categoryFilter   = document.getElementById('category-filter');
  const eventsListEl     = document.getElementById('events-list');

  function renderList(events) {
    if (events.length === 0) {
      eventsListEl.innerHTML = '<p class="no-results">No events match your search.</p>';
      return;
    }
    eventsListEl.innerHTML = events.map(ev => `
      <div class="event-card" data-id="${escHtml(ev.id)}">
        <div class="event-date">${escHtml(formatDateTime(ev.start, ev.end))}</div>
        <div class="event-title">${escHtml(ev.title)}</div>
        ${ev.venue ? `<div class="event-venue">📍 ${escHtml(ev.venue)}</div>` : ''}
        ${ev.description ? `<div class="event-desc">${escHtml(ev.description)}</div>` : ''}
        ${ev.category ? `<span class="event-badge">${escHtml(ev.category)}</span>` : ''}
      </div>
    `).join('');

    eventsListEl.querySelectorAll('.event-card').forEach(card => {
      card.addEventListener('click', () => {
        const ev = allEvents.find(e => e.id === card.dataset.id);
        if (ev) openModal(ev);
      });
    });
  }

  function filterAndRender() {
    const query    = searchInput.value.toLowerCase();
    const category = categoryFilter.value;
    const filtered = allEvents.filter(ev => {
      const matchesSearch = !query ||
        (ev.title       || '').toLowerCase().includes(query) ||
        (ev.description || '').toLowerCase().includes(query) ||
        (ev.venue       || '').toLowerCase().includes(query);
      const matchesCat = !category || ev.category === category;
      return matchesSearch && matchesCat;
    });
    // Sort ascending by start date
    filtered.sort((a, b) => new Date(a.start) - new Date(b.start));
    renderList(filtered);
  }

  searchInput.addEventListener('input',    filterAndRender);
  categoryFilter.addEventListener('change', filterAndRender);

  // ── FullCalendar ─────────────────────────────────────────────────────────────
  let calendar;

  function initCalendar(events) {
    const calEl = document.getElementById('calendar');
    calendar = new FullCalendar.Calendar(calEl, {
      initialView: 'dayGridMonth',
      headerToolbar: {
        left:   'prev,next today',
        center: 'title',
        right:  'dayGridMonth,listMonth'
      },
      height: 'auto',
      events: events.map(ev => ({
        id:    ev.id,
        title: ev.title,
        start: ev.start,
        end:   ev.end || undefined,
        color: categoryColor(ev.category),
        extendedProps: ev,
      })),
      eventClick: function (info) {
        openModal(info.event.extendedProps);
      },
      eventMouseEnter: function (info) {
        info.el.title = info.event.title;
      },
    });
    calendar.render();
  }

  // ── Load data ────────────────────────────────────────────────────────────────
  fetch('data/events.json')
    .then(r => {
      if (!r.ok) throw new Error('Failed to load events.json');
      return r.json();
    })
    .then(data => {
      allEvents = Array.isArray(data.events) ? data.events : (Array.isArray(data) ? data : []);

      // Show last-updated timestamp
      if (data.last_updated) {
        const lu = new Date(data.last_updated);
        document.getElementById('last-updated').textContent =
          'Updated ' + lu.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
      }

      initCalendar(allEvents);
      filterAndRender();
    })
    .catch(err => {
      console.warn('Could not load events:', err);
      // Show empty state gracefully
      eventsListEl.innerHTML = '<p class="no-results">Events are loading — check back soon.</p>';
    });

})();
