$(document).ready(function () {
    var entityId = $('body').data('id');

    $('.sheet-image-gallery').on('click', '.sheet-image-thumb', function () {
        const $thumb = $(this);
        const imageSrc = $thumb.data('image');
        const label = $thumb.data('label') || 'Character image';
        const $primary = $('#sheet-primary-image');
        if (!imageSrc || !$primary.length) {
            return;
        }
        $('.sheet-image-thumb').removeClass('active').attr('aria-selected', 'false');
        $thumb.addClass('active').attr('aria-selected', 'true');
        $primary.attr('src', imageSrc).attr('alt', label);
    });

    // Handle section expansion
    $('.section-header').on('click', function () {
        const section = $(this).closest('.section');
        const content = section.find('.section-content');
        const icon = $(this).find('.toggle-icon');

        content.slideToggle(200);
        icon.toggleClass('fa-chevron-down fa-chevron-up');
    });

    // Handle Tab Switching
    function openTab(evt, tabName) {
        $('.tabcontent').hide();
        $('.tablinks').removeClass('active');
        $('#' + tabName).show();
        $(evt.currentTarget).addClass('active');
    }

    var inventoryFilterState = { q: '', sort: 'name', dir: 'asc' };

    function isInventoryDm() {
        return $('#inventory-is-dm').val() === '1';
    }

    function applyInventoryFilter() {
        const $list = $('.unequipped-items-list');
        if (!$list.length) {
            return;
        }
        const q = String(inventoryFilterState.q || '').trim().toLowerCase();
        const sortKey = inventoryFilterState.sort || 'name';
        const dir = inventoryFilterState.dir === 'desc' ? -1 : 1;
        const $slots = $list.children('.inventory-slot');
        const slots = $slots.get();
        slots.sort(function (a, b) {
            const $a = $(a);
            const $b = $(b);
            const av = inventorySortValue($a, sortKey);
            const bv = inventorySortValue($b, sortKey);
            if (typeof av === 'string' || typeof bv === 'string') {
                return dir * String(av).localeCompare(String(bv), undefined, { sensitivity: 'base' });
            }
            if (av === bv) {
                return dir * String($a.data('item-label') || '').localeCompare(String($b.data('item-label') || ''), undefined, { sensitivity: 'base' });
            }
            return dir * (av - bv);
        });
        slots.forEach(function (slot) {
            $list.append(slot);
        });
        let visible = 0;
        $slots.each(function () {
            const $slot = $(this);
            const hay = [
                $slot.attr('data-item-label'),
                $slot.attr('data-item-name'),
                $slot.attr('data-container-label')
            ].join(' ').toLowerCase();
            const match = !q || hay.indexOf(q) !== -1;
            $slot.toggle(match);
            if (match) {
                visible += 1;
            }
        });
        let $empty = $list.children('.inventory-filter-empty');
        if (!$empty.length) {
            $empty = $('<li class="container-empty inventory-filter-empty">No matching items</li>');
            $list.append($empty);
        } else {
            $list.append($empty);
        }
        const hasSlots = $list.children('.inventory-slot').length > 0;
        $empty.toggle(hasSlots && visible === 0);
    }

    function inventorySortValue($slot, key) {
        if (key === 'price') {
            const value = parseFloat($slot.attr('data-cost'));
            return Number.isFinite(value) ? value : Number.POSITIVE_INFINITY;
        }
        if (key === 'weight') {
            const value = parseFloat($slot.attr('data-weight'));
            return Number.isFinite(value) ? value : Number.POSITIVE_INFINITY;
        }
        return String($slot.attr('data-item-label') || $slot.attr('data-item-name') || '').toLowerCase();
    }

    function restoreInventoryToolbar() {
        const $search = $('#inventory-search');
        const $sort = $('#inventory-sort');
        const $dir = $('#inventory-sort-dir');
        if ($search.length) {
            $search.val(inventoryFilterState.q);
        }
        if ($sort.length) {
            $sort.val(inventoryFilterState.sort);
        }
        if ($dir.length) {
            const descending = inventoryFilterState.dir === 'desc';
            $dir.attr('data-dir', descending ? 'desc' : 'asc');
            $dir.text(descending ? '▼' : '▲');
            $dir.attr('title', descending ? 'Sort descending' : 'Sort ascending');
            $dir.attr('aria-label', descending ? 'Sort descending' : 'Sort ascending');
        }
        applyInventoryFilter();
    }

    function refreshEquipment() {
        $.get('/equipment',
            {
                id: entityId
            },
            function (response) {
                $('.equipment-container').html(response);
                populateDmCatalog();
                restoreInventoryToolbar();
            });
    }

    function updateSheetHp(hp) {
        if (hp == null || hp === '') {
            return;
        }
        const $input = $('#current-hp-input');
        if ($input.length) {
            $input.val(hp);
        }
    }


    // Add click event listeners to spell cards using event delegation
    $('.spells-container-sheet').on('click', '.spell', function (event) {
        // Close other flipped cards
        $('.spell').not(this).removeClass('flipped');
        // Toggle the clicked card
        $(this).toggleClass('flipped');
        event.stopPropagation();

        // Scroll to the card if it's being flipped open
        if ($(this).hasClass('flipped')) {
            $('html, body').animate({
                scrollTop: $(this).offset().top - 100 // Adjust the offset as needed
            }, 500);
        }
    });

    // Close any flipped cards when clicking outside
    $(document).click(function (event) {
        if (!$(event.target).closest('.spell').length) {
            $('.spell').removeClass('flipped');
        }
    });

    // Prevent click events on the spell-back from propagating to the document
    $('.spell-back').click(function (event) {
        event.stopPropagation();
    });

    // Make spells focusable
    $('.spell').attr('tabindex', '0');

    // Add keydown event handler
    $('.spells-container-sheet').on('keydown', '.spell', function (event) {
        if (event.key === 'Enter' || event.key === ' ') {
            // Close other flipped cards
            $('.spell').not(this).removeClass('flipped');
            // Toggle the clicked card
            $(this).toggleClass('flipped');
            event.preventDefault();
        }
    });

    $('.equipment-container').on('submit', '.form-unequip', function (event) {
        event.preventDefault();
        var form = $(this);
        var url = form.attr('action');
        var data = form.serialize();
        $.post(url, data, function (response) {
            refreshEquipment();
        });
    });

    $(document).on('submit', '.form-equip', function (event) {
        event.preventDefault();
        var form = $(this);
        var url = form.attr('action');
        var data = form.serialize();
        $.post(url, data, function (response) {
            if (response && response.error) {
                alert(response.error);
                return;
            }
            closeItemCard();
            refreshEquipment();
        }).fail(function (xhr) {
            const message = (xhr.responseJSON && (xhr.responseJSON.error || xhr.responseJSON.message)) || 'Unable to equip that item.';
            alert(message);
        });
    });

    $(document).on('submit', '.form-use-item', function (event) {
        event.preventDefault();
        var form = $(this);
        var url = form.attr('action');
        var data = form.serialize();
        $.post(url, data, function (response) {
            if (response && response.error) {
                alert(response.error);
                return;
            }
            if (response && response.hp != null) {
                updateSheetHp(response.hp);
            }
            closeItemCard();
            refreshEquipment();
        }).fail(function (xhr) {
            const message = (xhr.responseJSON && (xhr.responseJSON.error || xhr.responseJSON.message)) || 'Unable to use that item.';
            alert(message);
        });
    });

    $(document).on('click', '.attunement-btn', function (event) {
        event.preventDefault();
        event.stopPropagation();
        const $btn = $(this);
        if ($btn.prop('disabled')) {
            return;
        }
        const payload = {
            id: $btn.attr('data-entity') || entityId,
            item_id: $btn.attr('data-item'),
            op: $btn.attr('data-attune-op') || 'attune'
        };
        if ($btn.attr('data-force')) {
            payload.force = 1;
        }
        const reopenCard = !$itemCardModal.is('[hidden]') ? payload.item_id : null;
        $btn.prop('disabled', true);
        $.post('/attunement', payload)
            .done(function (response) {
                if (response && response.error) {
                    alert(response.error);
                    return;
                }
                refreshEquipment();
                if (reopenCard) {
                    openItemCard(reopenCard);
                }
            })
            .fail(function (xhr) {
                const message = (xhr.responseJSON && xhr.responseJSON.error) || 'Unable to change attunement.';
                alert(message);
            })
            .always(function () {
                $btn.prop('disabled', false);
            });
    });

    $('.equipment-container').on('input', '#inventory-search', function () {
        inventoryFilterState.q = $(this).val() || '';
        applyInventoryFilter();
    });

    $('.equipment-container').on('change', '#inventory-sort', function () {
        inventoryFilterState.sort = $(this).val() || 'name';
        applyInventoryFilter();
    });

    $('.equipment-container').on('click', '#inventory-sort-dir', function () {
        inventoryFilterState.dir = inventoryFilterState.dir === 'desc' ? 'asc' : 'desc';
        restoreInventoryToolbar();
    });

    // Attach the openTab function to the window so it can be called inline
    window.openTab = openTab;

    // Open the default tab
    $('.tablinks').first().click();

    // Controller assignment functionality
    const controllerInput = $('#controller-input');
    const controllerSelect = $('#controller-select');
    const suggestionsList = $('#controller-suggestions');
    const controllersList = $('#controllers-list');
    let currentEntityId = $('body').data('id');

    // Handle controller select dropdown
    controllerSelect.on('change', function () {
        const username = $(this).val();
        if (username) {
            addController(username);
            $(this).val(''); // Reset dropdown
        }
    });

    // Handle input for auto-complete
    controllerInput.on('input', function () {
        const query = $(this).val().trim();
        if (query.length < 2) {
            suggestionsList.hide();
            return;
        }

        // Fetch suggestions from server
        $.get('/get_users', { query: query }, function (users) {
            suggestionsList.empty();
            users.forEach(user => {
                if (!controllersList.find(`li:contains('${user}')`).length) {
                    suggestionsList.append(`<div data-username="${user}">${user}</div>`);
                }
            });
            suggestionsList.show();
        });
    });

    // Handle suggestion selection
    suggestionsList.on('click', 'div', function () {
        const username = $(this).data('username');
        addController(username);
        controllerInput.val('');
        suggestionsList.hide();
    });

    // Handle removing a controller
    controllersList.on('click', '.remove-controller', function () {
        const username = $(this).data('username');
        removeController(username);
    });

    // Add a controller
    function addController(username) {
        $.post('/update_controller', {
            entity_uid: currentEntityId,
            controller: username,
            action: 'add'
        }, function (response) {
            if (response.status === 'ok') {
                controllersList.append(`
                    <li>
                        ${username}
                        <button class="btn btn-sm btn-danger remove-controller" data-username="${username}">×</button>
                    </li>
                `);
            }
        });
    }

    // Remove a controller
    function removeController(username) {
        $.post('/update_controller', {
            entity_uid: currentEntityId,
            controller: username,
            action: 'remove'
        }, function (response) {
            if (response.status === 'ok') {
                controllersList.find(`li:contains('${username}')`).remove();
            }
        });
    }

    // Close suggestions when clicking outside
    $(document).on('click', function (e) {
        if (!$(e.target).closest('.controller-assignment').length) {
            suggestionsList.hide();
        }
    });

    // Container Management Functionality
    let dmItemCatalog = [];

    function containerApiBase() {
        return isInventoryDm() ? '/dm/container' : '/inventory/container';
    }

    function containerDomId(containerName) {
        return containerName.replace(/\s+/g, '-');
    }

    function formatCapacityText(status) {
        if (!status) {
            return '';
        }
        const used = status.weight_used_lbs != null ? status.weight_used_lbs : 0;
        if (status.capacity_lbs != null) {
            let text = `${used} / ${status.capacity_lbs} lb`;
            if (status.capacity_cu_ft != null) {
                text += ` · ${status.capacity_cu_ft} cu ft`;
            }
            return text;
        }
        return `${used} lb carried`;
    }

    function updateCapacityUi(containerName, status) {
        const containerId = containerDomId(containerName);
        const containerDiv = $(`#container-${containerId}`);
        const capacityText = formatCapacityText(status);
        containerDiv.find('.container-capacity-text').text(capacityText);

        const bar = containerDiv.find('.container-capacity-bar');
        const fill = bar.find('.container-capacity-fill');
        if (status && status.capacity_lbs) {
            const pct = Math.min(100, Math.round((status.weight_used_lbs / status.capacity_lbs) * 100));
            fill.css('width', `${pct}%`);
            bar.removeClass('container-capacity-unknown');
            fill.toggleClass('container-capacity-warning', pct >= 85);
            fill.toggleClass('container-capacity-full', pct >= 100);
        } else {
            fill.css('width', '0%');
            bar.addClass('container-capacity-unknown');
        }
    }

    function renderContainerContents(containerDiv, entityUid, containerName, status) {
        const itemsList = containerDiv.find('.container-items-list');
        itemsList.empty();
        updateCapacityUi(containerName, status);

        const contents = (status && status.contents) || [];
        if (contents.length === 0) {
            itemsList.append('<li class="container-empty">Container is empty</li>');
            return;
        }

        contents.forEach(content => {
            const label = content.label || content.type;
            const itemName = content.type || content.name;
            const weightNote = content.weight_total != null ? ` · ${content.weight_total} lb` : '';
            const containerAttr = `data-container="${containerName}"`;
            let actions = `
                    <button type="button" class="btn btn-sm btn-secondary retrieve-container-item"
                            data-entity="${entityUid}"
                            data-container="${containerName}"
                            data-item="${itemName}"
                            data-qty="${content.qty || 1}">
                        Take Out
                    </button>`;
            if (content.usable) {
                actions = `
                    <form method="post" class="form-use-item" action="/inventory/use">
                        <input type="hidden" name="id" value="${entityUid}">
                        <input type="hidden" name="item_id" value="${itemName}">
                        <input type="hidden" name="container_name" value="${containerName}">
                        <button type="submit" class="inventory-action-btn inventory-action-btn-use">Use</button>
                    </form>` + actions;
            }
            if (content.equippable) {
                actions = `
                    <form method="post" class="form-equip" action="/equip">
                        <input type="hidden" name="id" value="${entityUid}">
                        <input type="hidden" name="item_id" value="${itemName}">
                        <input type="hidden" name="container_name" value="${containerName}">
                        <button type="submit" class="inventory-action-btn equip-button">Equip</button>
                    </form>` + actions;
            }
            itemsList.append(`
                <li class="container-item" data-item-name="${itemName}">
                    <span class="container-item-name item-card-trigger" data-item-name="${itemName}" ${containerAttr} role="button" tabindex="0" title="View item">${label}</span>
                    <span class="container-item-meta">x${content.qty || 1}${weightNote}</span>
                    ${actions}
                </li>
            `);
        });
    }

    function fetchContainerStatus(entityUid, containerName, onSuccess) {
        const url = isInventoryDm() ? `${containerApiBase()}/contents` : `${containerApiBase()}/status`;
        $.get(url, {
            entity_id: entityUid,
            container_name: containerName
        }, function(response) {
            if (response.success) {
                const status = response.status || { contents: response.contents || [] };
                onSuccess(status);
            } else {
                alert('Failed to load container: ' + (response.error || 'unknown error'));
            }
        }).fail(function() {
            alert('Error loading container');
        });
    }

    function populateStowSelect(stowableItems) {
        const select = $('#modal-stow-item');
        select.empty();
        select.append('<option value="">Choose an item…</option>');
        (stowableItems || []).forEach(item => {
            const weightNote = item.weight != null ? ` (${item.weight} lb each)` : '';
            select.append(
                `<option value="${item.name}">${item.label} x${item.qty}${weightNote}</option>`
            );
        });
        if (!stowableItems || stowableItems.length === 0) {
            select.append('<option value="" disabled>No stowable items in inventory</option>');
        }
    }

    function populateDmCatalog() {
        const datalist = $('#items-catalog');
        if (!datalist.length || !dmItemCatalog.length) {
            return;
        }
        datalist.empty();
        dmItemCatalog.forEach(item => {
            datalist.append(`<option value="${item.name}"></option>`);
        });
        $('#dm-free-text-group').show();
    }

    if (isInventoryDm()) {
        $.get('/dm/items_catalog', function(data) {
            if (data.items) {
                dmItemCatalog = data.items;
            } else if (data.success && data.items) {
                dmItemCatalog = data.items;
            }
            populateDmCatalog();
        });
        $('#dm-free-text-group').show();
    }

    function containerToggleLabel(btn, open) {
        const openLabel = btn.data('open-label') || 'Open';
        const closeLabel = btn.data('close-label') || 'Close';
        btn.text(open ? closeLabel : openLabel);
    }

    function setContainerExpanded(wrapper, expanded) {
        wrapper.closest('.inventory-slot').toggleClass('container-expanded', expanded);
    }

    $('.equipment-container').on('click', '.container-toggle-btn', function(e) {
        e.preventDefault();
        const btn = $(this);
        const entityUid = btn.data('entity');
        const containerName = btn.data('container');
        const containerId = containerDomId(containerName);
        const wrapper = $(`#container-wrapper-${containerId}`);
        const containerDiv = $(`#container-${containerId}`);

        if (wrapper.is(':visible')) {
            wrapper.slideUp(200, function() {
                setContainerExpanded(wrapper, false);
            });
            containerToggleLabel(btn, false);
            return;
        }

        fetchContainerStatus(entityUid, containerName, function(status) {
            renderContainerContents(containerDiv, entityUid, containerName, status);
            setContainerExpanded(wrapper, true);
            wrapper.slideDown(200);
            containerToggleLabel(btn, true);
        });
    });

    $('.equipment-container').on('click', '.container-close-btn', function(e) {
        e.preventDefault();
        const containerId = $(this).data('container');
        const wrapper = $(`#container-wrapper-${containerId}`);
        wrapper.slideUp(200, function() {
            setContainerExpanded(wrapper, false);
        });
        const btn = $(`.container-toggle-btn[data-container="${containerId.replace(/-/g, ' ')}"]`);
        containerToggleLabel(btn, false);
    });

    $('.equipment-container').on('click', '.container-add-btn', function(e) {
        e.preventDefault();
        const entityUid = $(this).data('entity');
        const containerName = $(this).data('container');

        $('#modal-entity-id').val(entityUid);
        $('#modal-container-name').val(containerName);
        $('#container-modal-title').text(`Stow Item in ${containerName}`);
        $('#modal-item-qty').val('1');
        $('#modal-item-name').val('');
        $('#modal-stow-item').val('');

        fetchContainerStatus(entityUid, containerName, function(status) {
            populateStowSelect(status.stowable_items || []);
            $('#container-modal').fadeIn(200);
        });
    });

    $('.equipment-container').on('click', '.modal-close', function() {
        $('#container-modal').fadeOut(200);
    });

    $('.equipment-container').on('submit', '#container-add-form', function(e) {
        e.preventDefault();
        const entityUid = $('#modal-entity-id').val();
        const containerName = $('#modal-container-name').val();
        const dmFreeText = ($('#modal-item-name').val() || '').trim();
        const selectedItem = ($('#modal-stow-item').val() || '').trim();
        const itemName = dmFreeText || selectedItem;
        const qty = parseInt($('#modal-item-qty').val(), 10) || 1;

        if (!itemName) {
            alert('Choose an item to stow.');
            return;
        }

        const postUrl = isInventoryDm() ? `${containerApiBase()}/add` : `${containerApiBase()}/stow`;
        const payload = isInventoryDm()
            ? { entity_id: entityUid, container_name: containerName, item_name: itemName, qty }
            : { entity_id: entityUid, container_name: containerName, item_name: itemName, qty };

        $.post(postUrl, payload, function(response) {
            if (response.success) {
                $('#container-modal').fadeOut(200);
                refreshEquipment();

                const containerId = containerDomId(containerName);
                const containerDiv = $(`#container-${containerId}`);
                if ($(`#container-wrapper-${containerId}`).is(':visible')) {
                    const status = response.status || { contents: [] };
                    renderContainerContents(containerDiv, entityUid, containerName, status);
                }
            } else {
                alert('Failed to stow item: ' + (response.error || 'unknown error'));
            }
        }).fail(function() {
            alert('Error stowing item');
        });
    });

    $('.equipment-container').on('click', '.retrieve-container-item', function(e) {
        e.preventDefault();
        const btn = $(this);
        const entityUid = btn.data('entity');
        const containerName = btn.data('container');
        const itemName = btn.data('item');
        const maxQty = parseInt(btn.data('qty'), 10) || 1;
        const label = btn.closest('.container-item').find('.container-item-name').text() || itemName;

        if (!confirm(`Take ${label} out of ${containerName}?`)) {
            return;
        }

        const postUrl = isInventoryDm() ? `${containerApiBase()}/remove` : `${containerApiBase()}/retrieve`;
        $.post(postUrl, {
            entity_id: entityUid,
            container_name: containerName,
            item_name: itemName,
            qty: maxQty
        }, function(response) {
            if (response.success) {
                refreshEquipment();
                const containerId = containerDomId(containerName);
                const containerDiv = $(`#container-${containerId}`);
                if ($(`#container-wrapper-${containerId}`).is(':visible')) {
                    const status = response.status || { contents: [] };
                    renderContainerContents(containerDiv, entityUid, containerName, status);
                }
            } else {
                alert('Failed to retrieve item: ' + (response.error || 'unknown error'));
            }
        }).fail(function() {
            alert('Error retrieving item');
        });
    });

    const $itemCardModal = $('#item-card-modal');
    const $itemCardBody = $itemCardModal.find('.item-card-modal-body');
    let itemCardRequest = null;

    function closeItemCard() {
        $itemCardModal.attr('hidden', true);
        $('body').removeClass('item-card-open');
        if (itemCardRequest && itemCardRequest.abort) {
            itemCardRequest.abort();
            itemCardRequest = null;
        }
    }

    function openItemCard(itemName, containerName) {
        if (!itemName || !entityId) {
            return;
        }
        $itemCardBody.html('<p class="item-card-loading">Loading item…</p>');
        $itemCardModal.removeAttr('hidden');
        $('body').addClass('item-card-open');
        if (itemCardRequest && itemCardRequest.abort) {
            itemCardRequest.abort();
        }
        const params = { id: entityId, item: itemName };
        if (containerName) {
            params.container = containerName;
        }
        itemCardRequest = $.get('/item_card', params)
            .done(function (html) {
                $itemCardBody.html(html);
            })
            .fail(function (xhr) {
                const message = (xhr.responseJSON && xhr.responseJSON.error) || 'Unable to load this item.';
                $itemCardBody.html('<p class="item-card-error">' + message + '</p>');
            })
            .always(function () {
                itemCardRequest = null;
            });
    }

    function itemNameFromTrigger(el) {
        const $el = $(el);
        return $el.attr('data-item-name') || $el.closest('[data-item-name]').attr('data-item-name');
    }

    function containerFromTrigger(el) {
        const $el = $(el);
        return $el.attr('data-container') || $el.closest('[data-container]').attr('data-container') || '';
    }

    $(document).on('click', '#Equipment .item-card-trigger, #item-card-modal .item-card-trigger', function (event) {
        if ($(event.target).closest('form, a, input, select, textarea').length) {
            return;
        }
        if ($(event.target).closest('button:not(.item-card-trigger)').length) {
            return;
        }
        event.preventDefault();
        event.stopPropagation();
        const itemName = itemNameFromTrigger(this);
        if (itemName) {
            openItemCard(itemName, containerFromTrigger(this));
        }
    });

    $(document).on('keydown', '#Equipment .item-card-trigger, #item-card-modal .item-card-trigger', function (event) {
        if (event.key !== 'Enter' && event.key !== ' ') {
            return;
        }
        event.preventDefault();
        const itemName = itemNameFromTrigger(this);
        if (itemName) {
            openItemCard(itemName, containerFromTrigger(this));
        }
    });

    $itemCardModal.on('click', '[data-item-card-dismiss], .item-card-modal-close', function (event) {
        event.preventDefault();
        closeItemCard();
    });

    $(document).on('keydown', function (event) {
        if (event.key === 'Escape' && !$itemCardModal.is('[hidden]')) {
            closeItemCard();
        }
    });
});