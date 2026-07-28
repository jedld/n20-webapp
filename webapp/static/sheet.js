$(document).ready(function () {
    var entityId = $('body').data('id');

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

    function refreshEquipment() {
        $.get('/equipment',
            {
                id: entityId
            },
            function (response) {
                $('.equipment-container').html(response);
            });
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
            // Update the spell list
            refreshEquipment();
        });
    });

    $('.equipment-container').on('submit', '.form-equip', function (event) {
        event.preventDefault();
        var form = $(this);
        var url = form.attr('action');
        var data = form.serialize();
        $.post(url, data, function (response) {
            // Update the spell list
            refreshEquipment();
        });
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
    const inventoryIsDm = $('#inventory-is-dm').val() === '1';
    const containerApiBase = inventoryIsDm ? '/dm/container' : '/inventory/container';

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
            const weightNote = content.weight_total != null ? ` · ${content.weight_total} lb` : '';
            itemsList.append(`
                <li class="container-item" data-item-name="${content.type}">
                    <span class="container-item-name">${label}</span>
                    <span class="container-item-meta">x${content.qty || 1}${weightNote}</span>
                    <button type="button" class="btn btn-sm btn-secondary retrieve-container-item"
                            data-entity="${entityUid}"
                            data-container="${containerName}"
                            data-item="${content.type}"
                            data-qty="${content.qty || 1}">
                        Take Out
                    </button>
                </li>
            `);
        });
    }

    function fetchContainerStatus(entityUid, containerName, onSuccess) {
        const url = inventoryIsDm ? `${containerApiBase}/contents` : `${containerApiBase}/status`;
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

    if (inventoryIsDm) {
        $.get('/dm/items_catalog', function(data) {
            const datalist = $('#items-catalog');
            if (data.success && data.items) {
                data.items.forEach(item => {
                    datalist.append(`<option value="${item.name}"></option>`);
                });
            }
        });
        $('#dm-free-text-group').show();
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
            wrapper.slideUp(200);
            btn.text('Open Backpack');
            return;
        }

        fetchContainerStatus(entityUid, containerName, function(status) {
            renderContainerContents(containerDiv, entityUid, containerName, status);
            wrapper.slideDown(200);
            btn.text('Close Backpack');
        });
    });

    $('.equipment-container').on('click', '.container-close-btn', function(e) {
        e.preventDefault();
        const containerId = $(this).data('container');
        $(`#container-wrapper-${containerId}`).slideUp(200);
        $(`.container-toggle-btn[data-container="${containerId.replace(/-/g, ' ')}"]`).text('Open Backpack');
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

    $('.modal-close').on('click', function() {
        $('#container-modal').fadeOut(200);
    });

    $('#container-add-form').on('submit', function(e) {
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

        const postUrl = inventoryIsDm ? `${containerApiBase}/add` : `${containerApiBase}/stow`;
        const payload = inventoryIsDm
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

        const postUrl = inventoryIsDm ? `${containerApiBase}/remove` : `${containerApiBase}/retrieve`;
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
});