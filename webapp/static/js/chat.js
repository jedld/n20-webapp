const Chat = Object.assign({}, window.LocalConversationChatBindings || {}, {
    renderedConversationReplyIds: new Set(),
    dismissedConversationBubbles: new Set(),
    conversationBubbleTimers: {},
    _ttsReplyPolls: new Set(),
    _ttsUiRetryKeys: new Set(),
    conversationBubbleKey: function (entity_id, message, reply_id) {
        if (reply_id) {
            return `reply:${reply_id}`;
        }
        const text = String(message || '').trim();
        return text ? `entity:${entity_id}:${text}` : `entity:${entity_id}`;
    },
    isConversationBubbleDismissed: function (entity_id, message, reply_id) {
        const key = Chat.conversationBubbleKey(entity_id, message, reply_id);
        return Chat.dismissedConversationBubbles.has(key);
    },
    markConversationBubbleDismissed: function (entity_id, message, reply_id) {
        const key = Chat.conversationBubbleKey(entity_id, message, reply_id);
        Chat.dismissedConversationBubbles.add(key);
    },
    clearConversationBubbleTimer: function (entity_id) {
        const timerId = Chat.conversationBubbleTimers[entity_id];
        if (timerId != null) {
            clearTimeout(timerId);
            delete Chat.conversationBubbleTimers[entity_id];
        }
    },
    scheduleConversationBubbleDismiss: function (entity_id, $tile, delayMs) {
        Chat.clearConversationBubbleTimer(entity_id);
        Chat.conversationBubbleTimers[entity_id] = setTimeout(() => {
            delete Chat.conversationBubbleTimers[entity_id];
            const $bubble = $tile.find('.conversation-bubble');
            if ($bubble.length) {
                $bubble.stop(true, true).fadeOut(500, function () {
                    $(this).remove();
                });
            }
        }, delayMs);
    },
    dismissConversationBubble: function (bubbleEl) {
        const $bubble = $(bubbleEl);
        if (!$bubble.length) {
            return;
        }
        const $tile = $bubble.closest('.tile');
        const entity_id = $tile.data('coords-id') || $tile.attr('data-coords-id');
        const reply_id = $bubble.attr('data-reply-id') || null;
        const message = $bubble.find('.bubble-content').text() || '';
        if (entity_id) {
            Chat.markConversationBubbleDismissed(entity_id, message, reply_id);
            Chat.clearConversationBubbleTimer(entity_id);
        }
        $bubble.stop(true, true).remove();
    },
    collectConversationBubbles: function () {
        const bubbles = [];
        $('.conversation-bubble').each(function () {
            const $bubble = $(this);
            const $tile = $bubble.closest('.tile');
            const entity_id = String($tile.data('coords-id') || $tile.attr('data-coords-id') || '').trim();
            if (!entity_id) {
                return;
            }
            bubbles.push({
                entity_id,
                message: $bubble.find('.bubble-content').text() || '',
                reply_id: $bubble.attr('data-reply-id') || null,
                minimized: $bubble.hasClass('minimized'),
                dialogTrigger: $bubble.hasClass('dialog-trigger'),
            });
        });
        return bubbles;
    },
    removeDismissedConversationBubbles: function () {
        $('.conversation-bubble').each(function () {
            const $bubble = $(this);
            const $tile = $bubble.closest('.tile');
            const entity_id = String($tile.data('coords-id') || $tile.attr('data-coords-id') || '').trim();
            const message = $bubble.find('.bubble-content').text() || '';
            const reply_id = $bubble.attr('data-reply-id') || null;
            if (entity_id && Chat.isConversationBubbleDismissed(entity_id, message, reply_id)) {
                $bubble.remove();
            }
        });
    },
    restoreConversationBubbles: function (bubbles) {
        (bubbles || []).forEach((entry) => {
            if (!entry || !entry.entity_id) {
                return;
            }
            if (Chat.isConversationBubbleDismissed(entry.entity_id, entry.message, entry.reply_id)) {
                return;
            }
            const $tile = $(`.tile[data-coords-id="${entry.entity_id}"]`);
            if (!$tile.length || $tile.find('.conversation-bubble').length) {
                return;
            }
            Chat.showConversationBubble(entry.entity_id, entry.message, {
                reply_id: entry.reply_id,
                dialogTrigger: entry.dialogTrigger,
                preserveMinimized: entry.minimized,
                preserveDismissTimer: true,
            });
        });
    },
    shouldSkipConversationReply: function (payload) {
        const replyId = payload && payload.reply_id;
        if (!replyId) {
            return false;
        }
        return Chat.renderedConversationReplyIds.has(replyId);
    },
    markConversationReplyRendered: function (payload) {
        const replyId = payload && payload.reply_id;
        if (replyId) {
            Chat.renderedConversationReplyIds.add(replyId);
        }
    },
    ingestTalkReplies: function (replies) {
        if (!Array.isArray(replies) || !replies.length) {
            return;
        }
        if (typeof window.ingestTalkConversationReplies === 'function') {
            window.ingestTalkConversationReplies(replies);
        }
    },
    dialogAutoContinueMs: 3500,
    dialogAutoContinueMax: 6,
    dialogContinuation: {
        timerId: null,
        continuationId: null,
        beatsRemaining: 0,
        autoCount: 0,
    },
    cancelDialogAutoContinue: function () {
        const state = Chat.dialogContinuation;
        if (state.timerId != null) {
            clearTimeout(state.timerId);
            state.timerId = null;
        }
    },
    resetDialogAutoContinue: function () {
        Chat.cancelDialogAutoContinue();
        Chat.dialogContinuation.continuationId = null;
        Chat.dialogContinuation.beatsRemaining = 0;
        Chat.dialogContinuation.autoCount = 0;
    },
    scheduleDialogAutoContinue: function (payload) {
        const continuationId = payload && payload.continuation_id;
        const beatsRemaining = Number((payload && payload.continuation_beats_remaining) || 0);
        if (!continuationId || beatsRemaining <= 0) {
            Chat.resetDialogAutoContinue();
            return;
        }
        if (!$('#jrpgDialogPanel').is(':visible')) {
            return;
        }
        Chat.cancelDialogAutoContinue();
        Chat.dialogContinuation.continuationId = continuationId;
        Chat.dialogContinuation.beatsRemaining = beatsRemaining;
        Chat.dialogContinuation.timerId = setTimeout(() => {
            Chat.dialogContinuation.timerId = null;
            const fireContinuation = () => Chat.requestDialogContinuation();
            if (window.TTSPlayer && typeof window.TTSPlayer.whenPlaybackIdle === 'function') {
                window.TTSPlayer.whenPlaybackIdle(fireContinuation);
            } else {
                fireContinuation();
            }
        }, Chat.dialogAutoContinueMs);
    },
    requestDialogContinuation: function () {
        const state = Chat.dialogContinuation;
        const continuationId = state.continuationId;
        if (!continuationId) {
            return;
        }
        if (state.autoCount >= Chat.dialogAutoContinueMax) {
            Chat.resetDialogAutoContinue();
            return;
        }
        if (typeof window !== 'undefined' && window.__n20_dialogMessageProcessing) {
            Chat.scheduleDialogAutoContinue({
                continuation_id: continuationId,
                continuation_beats_remaining: state.beatsRemaining,
            });
            return;
        }
        state.autoCount += 1;
        $.ajax({
            url: '/talk/continue',
            type: 'POST',
            contentType: 'application/json',
            timeout: 60000,
            data: JSON.stringify({ continuation_id: continuationId }),
            success: (data) => {
                if (!data || !data.success) {
                    Chat.resetDialogAutoContinue();
                    return;
                }
                Chat.ingestTalkReplies(data.replies);
                const nextId = data.continuation_id;
                const remaining = Number(data.continuation_beats_remaining || 0);
                if (nextId && remaining > 0) {
                    Chat.scheduleDialogAutoContinue({
                        continuation_id: nextId,
                        continuation_beats_remaining: remaining,
                    });
                } else {
                    Chat.resetDialogAutoContinue();
                }
            },
            error: () => {
                Chat.resetDialogAutoContinue();
            },
        });
    },
    parsePlayerConversationInput: function (text) {
        const raw = String(text || '').trim();
        if (!raw) {
            return { spoken: '', actions: [], raw: '' };
        }
        const actions = [];
        let working = raw;
        working = working.replace(/\[action:\s*([^\]]+)\]/gi, (_match, action) => {
            const cleaned = String(action || '').trim();
            if (cleaned) {
                actions.push(cleaned);
            }
            return ' ';
        });
        working = working.replace(/\*([^*]+)\*/g, (_match, action) => {
            const cleaned = String(action || '').trim();
            if (cleaned) {
                actions.push(cleaned);
            }
            return ' ';
        });
        const slashMatch = working.trim().match(/^\/(?:me|emote|action)\s+(.+)$/i);
        if (slashMatch) {
            const cleaned = String(slashMatch[1] || '').trim();
            if (cleaned) {
                actions.push(cleaned);
            }
            working = '';
        }
        const spoken = working.replace(/\s+/g, ' ').trim();
        return { spoken, actions, raw };
    },
    renderPlayerActionLines: function (actions) {
        const entries = Array.isArray(actions)
            ? actions.filter((entry) => `${entry || ''}`.trim().length > 0)
            : [];
        if (!entries.length) {
            return '';
        }
        return entries.map((entry) => (
            `<div class="dialog-action-line"><i class="glyphicon glyphicon-hand-right"></i> ${Chat.escapeHtml(entry)}</div>`
        )).join('');
    },
    // Helper function to add a processing message with animated dots
    addProcessingMessage: function (isDraggable = false) {
        const timestamp = new Date().toLocaleTimeString();
        const processingId = 'processing-' + Date.now();
        const $chatContainer = isDraggable ? $("#draggable-chat-messages") : $("#chat-messages");
        const messageHtml = `
      <div id="${processingId}" class="chat-message assistant processing">
        <strong>AI Assistant (${timestamp}):</strong>
        <span class="processing-text">Thinking</span><span class="processing-dots">...</span>
      </div>
    `;
        $chatContainer.append(messageHtml);
        // Scroll to bottom
        $chatContainer.scrollTop($chatContainer[0].scrollHeight);

        return processingId;
    },

    updateProcessingMessage: function (processingId, text, isDraggable = false) {
        const $processing = $(`#${processingId}`);
        if ($processing.length) {
            $processing.find('.processing-text').text(text);
        }
    },

    removeProcessingMessage: function (processingId, isDraggable = false) {
        $(`#${processingId}`).remove();
    },
    cleanThinkingTags: function (content) {
        if (!content || typeof content !== 'string') {
            return content;
        }

        // Remove thinking tags and their content
        let cleaned = content.replace(/<think>.*?<\/think>/gs, '');
        cleaned = cleaned.replace(/<reasoning>.*?<\/reasoning>/gs, '');
        cleaned = cleaned.replace(/<thought>.*?<\/thought>/gs, '');

        // Remove reasoning blocks that start with "Okay, so" or similar
        cleaned = cleaned.replace(/Okay, so.*?(?=\[FUNCTION_CALL:|$)/gs, '');
        cleaned = cleaned.replace(/Let me.*?(?=\[FUNCTION_CALL:|$)/gs, '');
        cleaned = cleaned.replace(/I need to.*?(?=\[FUNCTION_CALL:|$)/gs, '');

        // Clean up extra whitespace and newlines
        cleaned = cleaned.replace(/\n\s*\n/g, '\n');
        cleaned = cleaned.trim();

        return cleaned;
    },
    getTranscriptText: function (isDraggable = false) {
        const $chatContainer = isDraggable ? $("#draggable-chat-messages") : $("#chat-messages");
        return $chatContainer.find(".chat-message").map(function () {
            return $(this).text().replace(/\s+/g, " ").trim();
        }).get().filter(Boolean).join("\n\n");
    },
    selectTranscript: function (isDraggable = false) {
        const container = (isDraggable ? $("#draggable-chat-messages") : $("#chat-messages"))[0];
        if (!container) {
            return;
        }

        const selection = window.getSelection();
        const range = document.createRange();
        range.selectNodeContents(container);
        selection.removeAllRanges();
        selection.addRange(range);
    },
    copyTranscript: async function (isDraggable = false) {
        const transcript = Chat.getTranscriptText(isDraggable);
        const $button = isDraggable ? $("#draggable-copy-chat-transcript") : $("#copy-chat-transcript");

        if (!transcript) {
            return;
        }

        try {
            if (navigator.clipboard && navigator.clipboard.writeText) {
                await navigator.clipboard.writeText(transcript);
            } else {
                const $temp = $("<textarea>")
                    .css({
                        position: "fixed",
                        top: "-9999px",
                        left: "-9999px"
                    })
                    .val(transcript)
                    .appendTo("body");
                $temp[0].focus();
                $temp[0].select();
                document.execCommand("copy");
                $temp.remove();
            }

            if ($button.length) {
                const originalHtml = $button.html();
                $button.html('<i class="glyphicon glyphicon-ok"></i> Copied');
                setTimeout(function () {
                    $button.html(originalHtml);
                }, 1500);
            }
        } catch (error) {
            console.warn("Clipboard copy failed, selecting transcript instead", error);
            Chat.selectTranscript(isDraggable);
        }
    },
    addChatMessage: function (role, content, isDraggable = false) {
        const timestamp = new Date().toLocaleTimeString();
        let messageClass = "chat-message";
        let prefix = "";
        let $chatContainer;

        // Determine which chat container to use
        if (isDraggable) {
            $chatContainer = $("#draggable-chat-messages");
        } else {
            $chatContainer = $("#chat-messages");
        }

        // Clean thinking tags from content (safety measure)
        const cleanedContent = Chat.cleanThinkingTags(content);

        switch (role) {
            case "user":
                messageClass += " user";
                prefix = `<strong>You (${timestamp}):</strong>`;
                break;
            case "assistant":
                messageClass += " assistant";
                prefix = `<strong>AI Assistant (${timestamp}):</strong>`;
                break;
            case "system":
                messageClass += " system";
                prefix = `<strong>System (${timestamp}):</strong>`;
                break;
        }

        const messageHtml = `<div class="${messageClass}">${prefix} ${Chat.escapeHtml(cleanedContent)}</div>`;
        $chatContainer.append(messageHtml);

        // Scroll to bottom
        $chatContainer.scrollTop($chatContainer[0].scrollHeight);
    },
    assistantChatTargets: function () {
        const targets = [];
        if ($("#chat-messages").length) {
            targets.push(false);
        }
        if ($("#draggable-chat-messages").length) {
            targets.push(true);
        }
        return targets;
    },
    showAssistantWelcomeBanner: function (label) {
        const modelLabel = label || "ready";
        const welcomeText =
            "Ready (" +
            modelLabel +
            "). Uses the same server LLM as NPCs and dialogs. How can I help with your D&D game?";
        const welcome =
            '<div class="chat-message system"><strong>AI Assistant:</strong> ' +
            Chat.escapeHtml(welcomeText) +
            "</div>";
        Chat.assistantChatTargets().forEach(function (isDraggable) {
            const $chatContainer = isDraggable ? $("#draggable-chat-messages") : $("#chat-messages");
            $chatContainer.html(welcome);
        });
    },
    renderAssistantChatHistory: function (history) {
        Chat.assistantChatTargets().forEach(function (isDraggable) {
            const $chatContainer = isDraggable ? $("#draggable-chat-messages") : $("#chat-messages");
            $chatContainer.empty();
            history.forEach(function (msg) {
                if (msg && (msg.role === "user" || msg.role === "assistant")) {
                    Chat.addChatMessage(msg.role, msg.content, isDraggable);
                }
            });
        });
    },
    loadAssistantChatHistory: function (modelLabel) {
        $.ajax({
            type: "GET",
            url: "/ai/history",
            dataType: "json",
            timeout: 15000,
            success: function (data) {
                if (data && data.success && Array.isArray(data.history) && data.history.length > 0) {
                    Chat.renderAssistantChatHistory(data.history);
                    return;
                }
                Chat.showAssistantWelcomeBanner(modelLabel);
            },
            error: function () {
                Chat.showAssistantWelcomeBanner(modelLabel);
            },
        });
    },
    getCurrentPovEntity: () => {
        // Try to get from the floating portraits
        const $currentPov = $('#floating-entity-portraits .floating-entity-portrait.current-pov');
        if ($currentPov.length) {
            const id = $currentPov.data('id');
            if (id && id !== 'None') {
                return id;
            }
        }

        const povEntityId = $('body').data('pov-entity');

        if (povEntityId && povEntityId !== 'None') {
            return povEntityId;
        }

        // If we can't determine POV, return null
        return null;
    },
    escapeHtml: function (content) {
        return $('<div>').text(content || '').html();
    },
    showConversationBubble: function (entity_id, message, options) {
        options = options || {};
        const reply_id = options.reply_id || null;
        const dialogTrigger = !!options.dialogTrigger;
        const autoDismissMs = options.autoDismissMs || (dialogTrigger ? 15000 : 10000);

        if (Chat.isConversationBubbleDismissed(entity_id, message, reply_id)) {
            return;
        }

        const $tile = $(`.tile[data-coords-id="${entity_id}"]`);
        if (!$tile.length) {
            return;
        }

        let $bubble = $tile.find('.conversation-bubble');
        const safeMessage = Chat.escapeHtml(message);

        if ($bubble.length) {
            $bubble.attr('data-reply-id', reply_id || '');
            $bubble.find('.bubble-content').text(message);
            const keepMinimized = !!options.preserveMinimized || $bubble.hasClass('minimized');
            if (!keepMinimized) {
                $bubble.removeClass('minimized');
                $bubble.find('.bubble-content').show();
                $bubble.find('.bubble-minimized').hide();
            }
            $bubble.show();
        } else if (dialogTrigger) {
            $bubble = $(`
          <div class="conversation-bubble dialog-trigger" style="cursor: pointer;">
            <div class="bubble-content">${safeMessage}</div>
            <div class="bubble-minimized" style="display: none;">
              <i class="glyphicon glyphicon-comment"></i>
            </div>
            <div class="dialog-indicator" style="position: absolute; top: -5px; right: -5px; background: #4CAF50; color: white; border-radius: 50%; width: 20px; height: 20px; display: flex; align-items: center; justify-content: center; font-size: 12px; box-shadow: 0 2px 4px rgba(0,0,0,0.3);">
              <i class="glyphicon glyphicon-chevron-right"></i>
            </div>
            <button class="close-bubble" onclick="Utils.dismissBubble(this.parentElement); event.stopPropagation();">×</button>
          </div>
        `);
            $bubble.attr('data-reply-id', reply_id || '');
            if (typeof options.onDialogTriggerClick === 'function') {
                $bubble.on('click', function (e) {
                    if (!$(e.target).hasClass('close-bubble')) {
                        options.onDialogTriggerClick(e, $tile, $bubble);
                    }
                });
            }
            $tile.append($bubble);
        } else {
            $bubble = $(`
          <div class="conversation-bubble">
            <div class="bubble-content">${safeMessage}</div>
            <div class="bubble-minimized" style="display: none;">
              <i class="glyphicon glyphicon-comment"></i>
            </div>
            <button class="close-bubble" onclick="Utils.dismissBubble(this.parentElement); event.stopPropagation();">×</button>
          </div>
        `);
            $bubble.attr('data-reply-id', reply_id || '');
            $tile.append($bubble);
        }

        if (!options.preserveDismissTimer) {
            Chat.scheduleConversationBubbleDismiss(entity_id, $tile, autoDismissMs);
        }
        if (options.preserveMinimized && $bubble && $bubble.length) {
            $bubble.addClass('minimized');
            $bubble.find('.bubble-content').hide();
            $bubble.find('.bubble-minimized').show();
        }
    },
    renderInsightCheckBadge: function (insightCheck) {
        if (!insightCheck || !insightCheck.assessment) {
            return '';
        }
        const assessmentClass = {
            'truthful': 'insight-truthful',
            'lie': 'insight-lie',
            'uncertain': 'insight-uncertain',
        }[insightCheck.assessment] || 'insight-uncertain';

        const assessmentLabel = {
            'truthful': '<i class="glyphicon glyphicon-ok"></i> Appears truthful',
            'lie': '<i class="glyphicon glyphicon-remove"></i> Appears deceptive',
            'uncertain': '<i class="glyphicon glyphicon-question-sign"></i> Uncertain',
        }[insightCheck.assessment] || 'Unknown';

        const rollText = insightCheck.roll_total
            ? `<span class="insight-roll">Rolled ${Chat.escapeHtml(String(insightCheck.roll_total))}</span>`
            : '';

        const targetText = insightCheck.target
            ? ` on ${Chat.escapeHtml(insightCheck.target)}`
            : '';

        const reasonHtml = insightCheck.reason
            ? `<div class="insight-reason">${Chat.escapeHtml(insightCheck.reason)}</div>`
            : '';

        return `
          <div class="insight-check-result ${assessmentClass}">
            <div class="insight-check-header">
              <span class="insight-check-label">Insight check${targetText}:</span>
              <span class="insight-check-verdict">${assessmentLabel}</span>
            </div>
            ${rollText}
            ${reasonHtml}
          </div>
        `;
    },
    insightCheckDedupeKey: function (insightCheck) {
        if (!insightCheck || !insightCheck.assessment) {
            return null;
        }
        return [
            insightCheck.assessment,
            insightCheck.roll_total == null ? '' : String(insightCheck.roll_total),
            insightCheck.target || '',
            insightCheck.reason || '',
        ].join('|');
    },
    shouldRenderInsightCheck: function (insightCheck) {
        const key = Chat.insightCheckDedupeKey(insightCheck);
        if (!key) {
            return false;
        }
        if (Chat._lastInsightDedupeKey === key) {
            return false;
        }
        Chat._lastInsightDedupeKey = key;
        return true;
    },
    addDialogInsightResult: function (insightCheck, messageText, options) {
        options = options || {};
        if (!insightCheck || !insightCheck.assessment) {
            return false;
        }
        if (options.attach_to_message_key
            && Chat.applyInsightResultToDialogMessage(options.attach_to_message_key, insightCheck)) {
            return true;
        }
        if (!Chat.shouldRenderInsightCheck(insightCheck)) {
            return false;
        }
        const content = (messageText && String(messageText).trim())
            ? String(messageText).trim()
            : (insightCheck.roll_total != null
                ? `Insight check: ${insightCheck.roll_total}${insightCheck.target ? ` on ${insightCheck.target}` : ''}.`
                : 'Insight check result.');
        Chat.addDialogMessage('system', content, 'system', {
            displayName: 'Dungeon Master',
            insight_check: insightCheck,
        });
        return true;
    },
    renderPerceptionCheckBadge: function (perceptionCheck) {
        if (!perceptionCheck || !perceptionCheck.description) {
            return '';
        }
        const rollText = perceptionCheck.roll_total
            ? `<span class="perception-roll">Rolled ${Chat.escapeHtml(String(perceptionCheck.roll_total))}</span>`
            : '';
        const targetText = perceptionCheck.target
            ? ` studying ${Chat.escapeHtml(perceptionCheck.target)}`
            : '';
        const focusText = perceptionCheck.focus
            ? `<div class="perception-focus">Focus: ${Chat.escapeHtml(perceptionCheck.focus)}</div>`
            : '';
        const descriptionHtml = `<div class="perception-description">${Chat.escapeHtml(perceptionCheck.description)}</div>`;
        return `
          <div class="perception-check-result">
            <div class="perception-check-header">
              <span class="perception-check-label">Perception check${targetText}</span>
            </div>
            ${rollText}
            ${focusText}
            ${descriptionHtml}
          </div>
        `;
    },
    perceptionCheckDedupeKey: function (perceptionCheck) {
        if (!perceptionCheck || !perceptionCheck.description) {
            return null;
        }
        return [
            perceptionCheck.roll_total == null ? '' : String(perceptionCheck.roll_total),
            perceptionCheck.target || '',
            perceptionCheck.focus || '',
            perceptionCheck.description || '',
        ].join('|');
    },
    shouldRenderPerceptionCheck: function (perceptionCheck, scope, options) {
        options = options || {};
        const key = Chat.perceptionCheckDedupeKey(perceptionCheck);
        if (!key) {
            return false;
        }
        const storeName = scope === 'local'
            ? '_perceptionDedupeKeysLocal'
            : '_perceptionDedupeKeysDialog';
        if (!Chat[storeName]) {
            Chat[storeName] = new Set();
        }
        if (!options.force && Chat[storeName].has(key)) {
            return false;
        }
        Chat[storeName].add(key);
        return true;
    },
    addDialogPerceptionResult: function (perceptionCheck, messageText, options) {
        options = options || {};
        if (!perceptionCheck || !perceptionCheck.description) {
            return false;
        }
        if (!Chat.shouldRenderPerceptionCheck(perceptionCheck, 'dialog', options)) {
            return false;
        }
        const content = (messageText && String(messageText).trim())
            ? String(messageText).trim()
            : (perceptionCheck.roll_total != null
                ? `Perception check: ${perceptionCheck.roll_total}${perceptionCheck.target ? ` studying ${perceptionCheck.target}` : ''}.`
                : 'Perception check result.');
        Chat.addDialogMessage('system', content, 'system', {
            displayName: 'Dungeon Master',
            perception_check: perceptionCheck,
        });
        return true;
    },
    _sha256Ascii: function (ascii) {
        function rightRotate(value, amount) {
            return (value >>> amount) | (value << (32 - amount));
        }

        const mathPow = Math.pow;
        const maxWord = mathPow(2, 32);
        const lengthProperty = 'length';
        let i;
        let j;
        let result = '';
        const words = [];
        const asciiBitLength = ascii[lengthProperty] * 8;
        let hash = Chat._sha256Ascii.h = Chat._sha256Ascii.h || [];
        const k = Chat._sha256Ascii.k = Chat._sha256Ascii.k || [];
        let primeCounter = k[lengthProperty];
        const isComposite = {};
        for (let candidate = 2; primeCounter < 64; candidate++) {
            if (!isComposite[candidate]) {
                for (i = 0; i < 313; i += candidate) {
                    isComposite[i] = candidate;
                }
                hash[primeCounter] = (mathPow(candidate, 0.5) * maxWord) | 0;
                k[primeCounter++] = (mathPow(candidate, 1 / 3) * maxWord) | 0;
            }
        }

        ascii += '\x80';
        while (ascii[lengthProperty] % 64 - 56) {
            ascii += '\x00';
        }
        for (i = 0; i < ascii[lengthProperty]; i++) {
            j = ascii.charCodeAt(i);
            if (j >> 8) {
                return '';
            }
            words[i >> 2] |= j << ((3 - i) % 4) * 8;
        }
        words[words[lengthProperty]] = ((asciiBitLength / maxWord) | 0);
        words[words[lengthProperty]] = asciiBitLength;

        for (j = 0; j < words[lengthProperty];) {
            const w = words.slice(j, j += 16);
            const oldHash = hash;
            hash = hash.slice(0, 8);

            for (i = 0; i < 64; i++) {
                const w15 = w[i - 15];
                const w2 = w[i - 2];
                const a = hash[0];
                const e = hash[4];
                const temp1 = hash[7]
                    + (rightRotate(e, 6) ^ rightRotate(e, 11) ^ rightRotate(e, 25))
                    + ((e & hash[5]) ^ ((~e) & hash[6]))
                    + k[i]
                    + (w[i] = (i < 16) ? w[i] : (
                        w[i - 16]
                        + (rightRotate(w15, 7) ^ rightRotate(w15, 18) ^ (w15 >>> 3))
                        + w[i - 7]
                        + (rightRotate(w2, 17) ^ rightRotate(w2, 19) ^ (w2 >>> 10))
                    ) | 0);
                const temp2 = (rightRotate(a, 2) ^ rightRotate(a, 13) ^ rightRotate(a, 22))
                    + ((a & hash[1]) ^ (a & hash[2]) ^ (hash[1] & hash[2]));
                hash = [(temp1 + temp2) | 0].concat(hash);
                hash[4] = (hash[4] + temp1) | 0;
            }

            for (i = 0; i < 8; i++) {
                hash[i] = (hash[i] + oldHash[i]) | 0;
            }
        }

        for (i = 0; i < 8; i++) {
            for (j = 3; j + 1; j--) {
                const b = (hash[i] >> (j * 8)) & 255;
                result += (b < 16 ? '0' : '') + b.toString(16);
            }
        }
        return result;
    },
    _sha256HexUtf8: function (value) {
        const bytes = new TextEncoder().encode(String(value));
        let binary = '';
        for (let i = 0; i < bytes.length; i++) {
            binary += String.fromCharCode(bytes[i]);
        }
        return Chat._sha256Ascii(binary);
    },
    insightCheckMessageKey: function (targetUid, statement) {
        const target = String(targetUid || '').trim();
        const text = String(statement || '').trim();
        return Chat._sha256HexUtf8(`${target}\0${text}`).slice(0, 16);
    },
    dialogInsightTrackingKey: function (targetUid, statement) {
        return Chat.insightCheckMessageKey(targetUid, statement);
    },
    markDialogInsightChecked: function (messageKey) {
        if (!messageKey) {
            return;
        }
        Chat._dialogInsightCheckedKeys = Chat._dialogInsightCheckedKeys || new Set();
        Chat._dialogInsightCheckedKeys.add(messageKey);
    },
    isDialogInsightChecked: function (messageKey) {
        if (!messageKey) {
            return false;
        }
        Chat._dialogInsightCheckedKeys = Chat._dialogInsightCheckedKeys || new Set();
        return Chat._dialogInsightCheckedKeys.has(messageKey);
    },
    renderDialogInsightButton: function (messageKey, targetUid, statement) {
        if (!messageKey || !targetUid || !statement) {
            return '';
        }
        return `
          <button type="button"
                  class="btn btn-xs btn-default dialog-insight-btn"
                  title="Roll Insight on this line"
                  data-message-key="${Chat.escapeHtml(messageKey)}"
                  data-target-id="${Chat.escapeHtml(String(targetUid))}"
                  data-statement="${Chat.escapeHtml(String(statement))}">
            <i class="glyphicon glyphicon-eye-open"></i> Insight Check
          </button>
        `;
    },
    applyInsightResultToDialogMessage: function (messageKey, insightCheck) {
        if (!messageKey || !insightCheck || !insightCheck.assessment) {
            return false;
        }
        const $msg = $(`#dialogChatMessages .dialog-chat-message[data-message-key="${messageKey}"]`);
        if (!$msg.length) {
            return false;
        }
        $msg.attr('data-insight-checked', 'true');
        $msg.find('.dialog-insight-btn').remove();
        $msg.find('.insight-check-result').remove();
        $msg.append(Chat.renderInsightCheckBadge(insightCheck));
        Chat.markDialogInsightChecked(messageKey);
        Chat.refreshDialogInsightButton();
        return true;
    },
    refreshDialogInsightButton: function () {
        const $container = $('#dialogChatMessages');
        if (!$container.length) {
            return;
        }
        $container.find('.dialog-insight-btn').remove();
        if (!Chat.getCurrentPovEntity()) {
            return;
        }
        const $lastEntity = $container.find('.dialog-chat-message.entity').last();
        if (!$lastEntity.length) {
            return;
        }
        if ($lastEntity.attr('data-insight-checked') === 'true'
            || $lastEntity.find('.insight-check-result').length) {
            return;
        }
        const messageKey = $lastEntity.attr('data-message-key');
        const targetUid = $lastEntity.attr('data-target-id');
        const statement = $lastEntity.find('.message-content').text();
        if (!messageKey || !targetUid || !statement
            || Chat.isDialogInsightChecked(messageKey)) {
            return;
        }
        $lastEntity.append(Chat.renderDialogInsightButton(messageKey, targetUid, statement));
        Chat.bindDialogInsightButtons($container);
    },
    bindDialogInsightButtons: function ($root) {
        const $scope = ($root && $root.length) ? $root : $('#dialogChatMessages');
        $scope.off('click.dialogInsight', '.dialog-insight-btn')
            .on('click.dialogInsight', '.dialog-insight-btn', function (event) {
                event.preventDefault();
                event.stopPropagation();
                const $btn = $(this);
                if (typeof window.sendDialogInsightCheck === 'function') {
                    window.sendDialogInsightCheck({
                        messageKey: $btn.attr('data-message-key'),
                        targetId: $btn.attr('data-target-id'),
                        statement: $btn.attr('data-statement'),
                    });
                }
            });
    },
    renderSkillCheckRequestBadge: function (skillCheckRequest) {
        if (!skillCheckRequest || !skillCheckRequest.skill) {
            return '';
        }
        const skillLabel = skillCheckRequest.skill_label
            || String(skillCheckRequest.skill).replace(/_/g, ' ').replace(/\b\w/g, (ch) => ch.toUpperCase());
        const requesterName = Chat.escapeHtml(skillCheckRequest.requester_name || 'NPC');
        const dcText = skillCheckRequest.dc != null ? ` (DC ${Chat.escapeHtml(String(skillCheckRequest.dc))})` : '';
        const pov = Chat.getCurrentPovEntity();
        const actorUid = skillCheckRequest.actor_uid;
        const canRoll = !!(pov && actorUid && pov === actorUid);
        const prompt = `${requesterName} requests a ${Chat.escapeHtml(skillLabel)} check${dcText}.`;

        if (!canRoll) {
            return `
              <div class="skill-check-request skill-check-request-info">
                <span class="skill-check-request-label">${prompt}</span>
              </div>
            `;
        }

        const roll = Chat.escapeHtml(String(skillCheckRequest.roll || '1d20'));
        const description = Chat.escapeHtml(String(skillCheckRequest.description || `${skillLabel} check`));
        const replyId = skillCheckRequest.reply_id || '';
        return `
          <div class="skill-check-request">
            <span class="skill-check-request-label">${prompt}</span>
            <button type="button"
                    class="btn btn-xs btn-primary dialog-skill-check-btn"
                    title="Roll ${Chat.escapeHtml(skillLabel)}"
                    data-actor-uid="${Chat.escapeHtml(String(actorUid))}"
                    data-requester-uid="${Chat.escapeHtml(String(skillCheckRequest.requester_uid || ''))}"
                    data-skill="${Chat.escapeHtml(String(skillCheckRequest.skill || ''))}"
                    data-dc="${skillCheckRequest.dc == null ? '' : Chat.escapeHtml(String(skillCheckRequest.dc))}"
                    data-reply-id="${Chat.escapeHtml(String(replyId))}"
                    data-roll="${roll}"
                    data-description="${description}">
              <i class="glyphicon glyphicon-certificate"></i> Roll ${Chat.escapeHtml(skillLabel)}
            </button>
          </div>
        `;
    },
    renderSkillCheckResultBadge: function (skillCheckResult) {
        if (!skillCheckResult || skillCheckResult.roll_total == null) {
            return '';
        }
        const skillLabel = skillCheckResult.skill_label
            || String(skillCheckResult.skill || 'Skill').replace(/_/g, ' ').replace(/\b\w/g, (ch) => ch.toUpperCase());
        const dcText = skillCheckResult.dc != null ? ` vs DC ${skillCheckResult.dc}` : '';
        let resultClass = 'skill-check-neutral';
        let verdict = `Rolled ${skillCheckResult.roll_total}${dcText}`;
        if (skillCheckResult.success === true) {
            resultClass = 'skill-check-success';
            verdict = `Success: ${skillCheckResult.roll_total}${dcText}`;
        } else if (skillCheckResult.success === false) {
            resultClass = 'skill-check-failure';
            verdict = `Failure: ${skillCheckResult.roll_total}${dcText}`;
        }
        return `
          <div class="skill-check-result ${resultClass}">
            <div class="skill-check-header">
              <span class="skill-check-label">${Chat.escapeHtml(skillLabel)} check:</span>
              <span class="skill-check-verdict">${Chat.escapeHtml(verdict)}</span>
            </div>
          </div>
        `;
    },
    applySkillCheckResultToDialogMessage: function (replyId, skillCheckResult) {
        if (!replyId || !skillCheckResult || skillCheckResult.roll_total == null) {
            return false;
        }
        const $msg = $(Chat.ttsReplySelector(replyId));
        if (!$msg.length) {
            return false;
        }
        $msg.attr('data-skill-checked', 'true');
        $msg.find('.dialog-skill-check-btn').remove();
        $msg.find('.skill-check-result').remove();
        $msg.append(Chat.renderSkillCheckResultBadge(skillCheckResult));
        return true;
    },
    bindSkillCheckButtons: function ($root) {
        const $scope = ($root && $root.length) ? $root : $('#dialogChatMessages');
        $scope.off('click.dialogSkillCheck', '.dialog-skill-check-btn')
            .on('click.dialogSkillCheck', '.dialog-skill-check-btn', function (event) {
                event.preventDefault();
                event.stopPropagation();
                const $btn = $(this);
                if ($btn.prop('disabled')) {
                    return;
                }
                if (typeof window.sendDialogSkillCheck === 'function') {
                    window.sendDialogSkillCheck({
                        requesterUid: $btn.attr('data-requester-uid'),
                        skill: $btn.attr('data-skill'),
                        dc: $btn.attr('data-dc'),
                        replyId: $btn.attr('data-reply-id'),
                    });
                }
            });
    },
    ttsReplySelector: function (replyId) {
        const token = String(replyId || '').trim();
        if (!token) {
            return '';
        }
        const escaped = (typeof CSS !== 'undefined' && CSS.escape)
            ? CSS.escape(token)
            : token.replace(/\\/g, '\\\\').replace(/"/g, '\\"');
        return `.dialog-chat-message[data-reply-id="${escaped}"], .local-conversation-message[data-reply-id="${escaped}"]`;
    },
    ttsPendingButtonSelector: function (replyId) {
        const token = String(replyId || '').trim();
        if (!token) {
            return '';
        }
        const escaped = (typeof CSS !== 'undefined' && CSS.escape)
            ? CSS.escape(token)
            : token.replace(/\\/g, '\\\\').replace(/"/g, '\\"');
        return `.tts-pending-btn[data-reply-id="${escaped}"], .tts-streaming-btn[data-reply-id="${escaped}"]`;
    },
    findTtsPendingButtons: function (replyId) {
        const selector = Chat.ttsPendingButtonSelector(replyId);
        if (!selector || typeof $ === 'undefined') {
            return $();
        }
        return $(selector);
    },
    findTtsMessageRoots: function (replyId) {
        const selector = Chat.ttsReplySelector(replyId);
        if (!selector || typeof $ === 'undefined') {
            return $();
        }
        return $(selector);
    },
    replaceTtsPendingButtons: function (replyId, audioUrl, meta, options) {
        const token = String(replyId || '').trim();
        if (!token || typeof $ === 'undefined') {
            return false;
        }
        const $buttons = Chat.findTtsPendingButtons(token);
        if (!$buttons.length) {
            return false;
        }
        meta = meta || {};
        options = Object.assign({ replyId: token }, options || {});
        let updated = false;
        $buttons.each(function () {
            const $btn = $(this);
            const $header = $(this.parentElement || this.parentNode);
            $btn.remove();
            if (audioUrl) {
                $header.append(Chat.renderTtsReplayButton(audioUrl, meta, options));
            }
            updated = true;
        });
        if (updated && audioUrl) {
            Chat.bindTtsReplayButtons($(document));
        }
        return updated;
    },
    clearTtsPendingUi: function (replyId) {
        let cleared = false;
        const $messages = Chat.findTtsMessageRoots(replyId);
        $messages.each(function () {
            const $header = $(this).find('.message-sender, .local-conversation-message-header > span').first();
            if (!$header.length) {
                return;
            }
            if ($header.find('.tts-pending-btn, .tts-streaming-btn').length) {
                $header.find('.tts-pending-btn, .tts-streaming-btn').remove();
                cleared = true;
            }
        });
        if (Chat.replaceTtsPendingButtons(replyId, null, {}, {})) {
            cleared = true;
        }
        return cleared;
    },
    upgradeTtsReplyUi: function (payload) {
        payload = payload || {};
        const replyId = payload.reply_id || payload.replyId;
        const audioUrl = payload.audio_url || payload.audioUrl;
        if (!replyId || !audioUrl) {
            return false;
        }
        const meta = {
            npcName: payload.speaker_name || payload.npc_name || 'NPC',
            npcUid: payload.entity_id || payload.npc_uid || '',
            emotion: payload.tts_emotion || payload.emotion || 'calm',
        };
        const options = {
            replyId: replyId,
            ttsGain: payload.tts_gain,
        };
        let updated = false;
        if (Chat.replaceTtsPendingButtons(replyId, audioUrl, meta, options)) {
            updated = true;
        }
        const $messages = Chat.findTtsMessageRoots(replyId);
        $messages.each(function () {
            const $container = $(this);
            const $header = $container.find('.message-sender, .local-conversation-message-header > span').first();
            if (!$header.length) {
                return;
            }
            $header.find('.tts-replay-btn, .tts-pending-btn, .tts-manual-btn, .tts-streaming-btn').remove();
            $header.append(Chat.renderTtsReplayButton(audioUrl, meta, options));
            updated = true;
        });
        if (updated) {
            Chat.bindTtsReplayButtons($messages.length ? $messages : $(document));
        }
        return updated;
    },
    scheduleTtsUiRetry: function (replyId, payload, attempt) {
        const token = String(replyId || '').trim();
        if (!token) {
            return;
        }
        const retryKey = `ui:${token}`;
        attempt = attempt || 0;
        if (attempt > 40) {
            Chat._ttsUiRetryKeys.delete(retryKey);
            return;
        }
        if (attempt === 0 && Chat._ttsUiRetryKeys.has(retryKey)) {
            return;
        }
        if (attempt === 0) {
            Chat._ttsUiRetryKeys.add(retryKey);
        }
        setTimeout(function () {
            if (Chat.upgradeTtsReplyUi(payload)) {
                Chat._ttsUiRetryKeys.delete(retryKey);
                return;
            }
            Chat.scheduleTtsUiRetry(token, payload, attempt + 1);
        }, attempt === 0 ? 100 : 250);
    },
    renderTtsReplayButton: function (audioUrl, meta, options) {
        options = options || {};
        if (options.ttsSkipped || options.tts_skipped) {
            return '';
        }
        const status = options.ttsStatus || options.tts_status || (audioUrl ? 'ready' : '');
        const replyId = options.replyId || options.reply_id || '';
        const safeReplyId = replyId ? Chat.escapeHtml(String(replyId)) : '';
        const npcName = Chat.escapeHtml(String((meta && meta.npcName) || ''));
        const npcUid = Chat.escapeHtml(String((meta && meta.npcUid) || ''));
        const emotion = Chat.escapeHtml(String((meta && meta.emotion) || 'calm'));
        const ttsText = Chat.escapeHtml(String(options.ttsText || options.tts_text || ''));
        const ttsGain = options.ttsGain !== undefined && options.ttsGain !== null
            ? String(options.ttsGain)
            : (options.tts_gain !== undefined && options.tts_gain !== null ? String(options.tts_gain) : '');

        if (status === 'pending') {
            return `
          <button type="button"
                  class="tts-replay-btn tts-pending-btn"
                  title="Voice rendering..."
                  aria-label="Voice rendering"
                  disabled
                  data-reply-id="${safeReplyId}">
            <i class="glyphicon glyphicon-refresh spinning"></i>
          </button>
        `;
        }

        if (status === 'streaming') {
            return `
          <button type="button"
                  class="tts-replay-btn tts-streaming-btn"
                  title="Voice streaming..."
                  aria-label="Voice streaming"
                  disabled
                  data-reply-id="${safeReplyId}">
            <i class="glyphicon glyphicon-volume-up tts-streaming-pulse"></i>
          </button>
        `;
        }

        if (status === 'manual') {
            return `
          <button type="button"
                  class="tts-replay-btn tts-manual-btn"
                  title="Play voice"
                  aria-label="Play voice"
                  data-reply-id="${safeReplyId}"
                  data-npc-uid="${npcUid}"
                  data-npc-name="${npcName}"
                  data-emotion="${emotion}"
                  data-tts-text="${ttsText}"
                  data-tts-gain="${Chat.escapeHtml(ttsGain)}">
            <i class="glyphicon glyphicon-volume-up"></i>
          </button>
        `;
        }

        if (!audioUrl) {
            return '';
        }
        const safeUrl = Chat.escapeHtml(String(audioUrl));
        return `
          <button type="button"
                  class="tts-replay-btn"
                  title="Play voice"
                  aria-label="Play voice"
                  data-reply-id="${safeReplyId}"
                  data-audio-url="${safeUrl}"
                  data-npc-name="${npcName}"
                  data-npc-uid="${npcUid}"
                  data-emotion="${emotion}"
                  data-tts-gain="${Chat.escapeHtml(ttsGain)}">
            <i class="glyphicon glyphicon-volume-up"></i>
          </button>
        `;
    },
    applyTtsReady: function (payload) {
        payload = payload || {};
        const replyId = payload.reply_id || payload.replyId;
        const audioUrl = payload.audio_url || payload.audioUrl;
        if (!replyId) {
            return;
        }
        Chat._ttsReplyPolls.delete(`poll:${replyId}`);
        if (!audioUrl) {
            Chat.clearTtsPendingUi(replyId);
            Chat.scheduleTtsReplyPoll(replyId);
            return;
        }
        const updated = Chat.upgradeTtsReplyUi(payload);
        if (!updated) {
            Chat.scheduleTtsUiRetry(replyId, payload);
        } else {
            Chat._ttsUiRetryKeys.delete(`ui:${replyId}`);
        }
    },
    scheduleTtsReplyPoll: function (replyId) {
        const token = String(replyId || '').trim();
        if (!token || typeof $ === 'undefined' || !$.getJSON) {
            return;
        }
        const pollKey = `poll:${token}`;
        if (Chat._ttsReplyPolls.has(pollKey)) {
            return;
        }
        Chat._ttsReplyPolls.add(pollKey);
        const startedAt = Date.now();
        const poll = function () {
            if (!Chat._ttsReplyPolls.has(pollKey)) {
                return;
            }
            if (Date.now() - startedAt > 120000) {
                Chat._ttsReplyPolls.delete(pollKey);
                return;
            }
            $.getJSON(`/tts/reply/${encodeURIComponent(token)}`)
                .done(function (data) {
                    if (data && data.ready && (data.audio_url || data.audioUrl)) {
                        Chat._ttsReplyPolls.delete(pollKey);
                        const replyId = data.reply_id || data.replyId || token;
                        const deliveryMode = window.TTSPlayer
                            && typeof window.TTSPlayer.getDeliveryMode === 'function'
                            ? window.TTSPlayer.getDeliveryMode()
                            : '';
                        if (deliveryMode === 'stream') {
                            Chat.applyTtsReady(data);
                            return;
                        }
                        const alreadyPlayed = window.TTSPlayer
                            && typeof window.TTSPlayer.hasReplyPlayed === 'function'
                            && replyId
                            && window.TTSPlayer.hasReplyPlayed(replyId);
                        if (alreadyPlayed) {
                            Chat.applyTtsReady(data);
                            return;
                        }
                        if (window.TTSPlayer && typeof window.TTSPlayer.unlockAudioForPlayback === 'function') {
                            window.TTSPlayer.unlockAudioForPlayback();
                        }
                        if (window.TTSPlayer && typeof window.TTSPlayer.playReplyAudio === 'function') {
                            window.TTSPlayer.playReplyAudio(data);
                        } else if (window.TTSPlayer && typeof window.TTSPlayer.onTtsReady === 'function') {
                            window.TTSPlayer.onTtsReady(data);
                        } else {
                            Chat.applyTtsReady(data);
                        }
                        return;
                    }
                    setTimeout(poll, 2000);
                })
                .fail(function () {
                    setTimeout(poll, 2000);
                });
        };
        setTimeout(poll, 2000);
    },
    maybeScheduleTtsReplyPoll: function (options) {
        options = options || {};
        const replyId = options.reply_id || options.replyId;
        const status = options.tts_status || options.ttsStatus || '';
        if (!replyId || options.tts_skipped || options.ttsSkipped) {
            return;
        }
        if (status === 'pending') {
            Chat.scheduleTtsReplyPoll(replyId);
            return;
        }
        if (status === 'streaming') {
            const mode = window.TTSPlayer
                && typeof window.TTSPlayer.getDeliveryMode === 'function'
                ? window.TTSPlayer.getDeliveryMode()
                : '';
            // Socket PCM handles stream delivery; poll only as onStreamEnd fallback.
            if (mode !== 'stream') {
                Chat.scheduleTtsReplyPoll(replyId);
            }
        }
    },
    applyTtsStreaming: function (payload) {
        payload = payload || {};
        const replyId = payload.reply_id || payload.replyId;
        if (!replyId) {
            return;
        }
        const $messages = Chat.findTtsMessageRoots(replyId);
        $messages.each(function () {
            const $container = $(this);
            const $header = $container.find('.message-sender, .local-conversation-message-header > span').first();
            if (!$header.length) {
                return;
            }
            if ($header.find('.tts-streaming-btn').length) {
                return;
            }
            $header.find('.tts-replay-btn, .tts-pending-btn, .tts-manual-btn').remove();
            $header.append(Chat.renderTtsReplayButton(null, {
                npcName: payload.speaker_name || 'NPC',
                npcUid: payload.entity_id || '',
                emotion: payload.tts_emotion || 'calm',
            }, {
                replyId: replyId,
                ttsStatus: 'streaming',
            }));
        });
    },
    requestManualTts: function ($btn) {
        const npcUid = $btn.attr('data-npc-uid') || $btn.data('npc-uid');
        const text = $btn.attr('data-tts-text') || $btn.data('tts-text');
        const emotion = $btn.attr('data-emotion') || $btn.data('emotion') || 'calm';
        const npcName = $btn.attr('data-npc-name') || $btn.data('npc-name') || 'NPC';
        const ttsGain = $btn.attr('data-tts-gain') || $btn.data('tts-gain');
        if (!npcUid || !text) {
            return;
        }
        $btn.prop('disabled', true).addClass('tts-pending-btn');
        $btn.find('.glyphicon').removeClass('glyphicon-volume-up').addClass('glyphicon-refresh spinning');
        $.ajax({
            url: '/tts/generate',
            type: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({
                npc_uid: npcUid,
                text: text,
                emotion: emotion,
            }),
            success: function (response) {
                const audioUrl = response && (response.audio_url || response.audioUrl);
                if (!audioUrl) {
                    $btn.prop('disabled', false).removeClass('tts-pending-btn');
                    $btn.find('.glyphicon').removeClass('glyphicon-refresh spinning').addClass('glyphicon-volume-up');
                    return;
                }
                $btn.removeClass('tts-manual-btn tts-pending-btn').addClass('tts-replay-btn');
                $btn.prop('disabled', false);
                $btn.attr('data-audio-url', audioUrl);
                $btn.find('.glyphicon').removeClass('glyphicon-refresh spinning').addClass('glyphicon-volume-up');
                Chat.playMessageAudio(audioUrl, {
                    npcName: npcName,
                    npcUid: npcUid,
                    emotion: emotion,
                    ttsGain: ttsGain,
                });
            },
            error: function () {
                $btn.prop('disabled', false).removeClass('tts-pending-btn');
                $btn.find('.glyphicon').removeClass('glyphicon-refresh spinning').addClass('glyphicon-volume-up');
            },
        });
    },
    playMessageAudio: function (audioUrl, meta) {
        if (!audioUrl) {
            return;
        }
        try {
            if (typeof window.TTSPlayer !== 'undefined' && window.TTSPlayer) {
                const payload = Object.assign({}, meta || {}, {
                    audio_url: audioUrl,
                    npc_name: (meta && meta.npcName) || 'NPC',
                    npc_uid: (meta && meta.npcUid) || '',
                    emotion: (meta && meta.emotion) || 'calm',
                    tts_gain: meta && (meta.ttsGain !== undefined ? meta.ttsGain : meta.tts_gain),
                });
                if (typeof window.TTSPlayer.playUrl === 'function') {
                    window.TTSPlayer.playUrl(audioUrl, payload);
                    return;
                }
                if (typeof window.TTSPlayer.onNPCSpeech === 'function') {
                    window.TTSPlayer.onNPCSpeech(payload);
                }
            }
        } catch (err) {
            console.warn('[Chat] TTS replay failed:', err);
        }
    },
    bindTtsReplayButtons: function ($root) {
        const $scope = $root && $root.length ? $root : $(document);
        $scope.off('click.ttsReplay', '.tts-replay-btn').on('click.ttsReplay', '.tts-replay-btn', function (event) {
            event.preventDefault();
            event.stopPropagation();
            const $btn = $(this);
            if ($btn.hasClass('tts-manual-btn')) {
                Chat.requestManualTts($btn);
                return;
            }
            if ($btn.hasClass('tts-pending-btn') || $btn.prop('disabled')) {
                return;
            }
            // Prefer attr() over data() — jQuery caches .data() and can miss
            // freshly-injected data-audio-url attributes.
            const audioUrl = $btn.attr('data-audio-url') || $btn.data('audio-url');
            Chat.playMessageAudio(audioUrl, {
                npcName: $btn.attr('data-npc-name') || $btn.data('npc-name'),
                npcUid: $btn.attr('data-npc-uid') || $btn.data('npc-uid'),
                emotion: $btn.attr('data-emotion') || $btn.data('emotion'),
                ttsGain: $btn.attr('data-tts-gain') || $btn.data('tts-gain'),
            });
        });
        $scope.off('click.ttsManual', '.tts-manual-btn').on('click.ttsManual', '.tts-manual-btn', function (event) {
            event.preventDefault();
            event.stopPropagation();
            Chat.requestManualTts($(this));
        });
    },
    renderDialogueSegments: function (segments, options) {
        options = options || {};
        const speechClass = options.speechClass || 'message-content';
        const asideClass = options.asideClass || 'dialog-message-aside';
        if (!Array.isArray(segments) || !segments.length) {
            return '';
        }
        return segments.map((segment) => {
            const text = String((segment && (segment.text || segment.content)) || '').trim();
            if (!text) {
                return '';
            }
            const kind = String((segment && segment.type) || '').toLowerCase();
            if (kind === 'aside' || kind === 'narrative' || kind === 'stage_direction') {
                return `<div class="${asideClass}">${Chat.escapeHtml(text)}</div>`;
            }
            return `<div class="${speechClass}">${Chat.escapeHtml(text)}</div>`;
        }).join('');
    },
    addDialogMessage: function (sender, content, type, options) {
        const timestamp = new Date().toLocaleTimeString();
        options = options || {};
        const narrativeEntries = Array.isArray(options.narrative)
            ? options.narrative.filter((entry) => `${entry || ''}`.trim().length > 0)
            : [];
        const actionEntries = Array.isArray(options.actions)
            ? options.actions.filter((entry) => `${entry || ''}`.trim().length > 0)
            : [];
        const segmentHtml = Chat.renderDialogueSegments(options.segments);
        const useSegments = Boolean(segmentHtml);
        const narrativeHtml = useSegments ? '' : narrativeEntries.map((entry) => (
            `<div class="dialog-message-aside">${Chat.escapeHtml(entry)}</div>`
        )).join('');
        const actionHtml = (type === 'player')
            ? Chat.renderPlayerActionLines(actionEntries)
            : '';

        // Determine the display name based on mode and sender
        let displayName = sender;
        if (options.displayName) {
            displayName = options.displayName;
        } else if (talkToEntityMode) {
            if (sender === 'player') {
                // Get the current POV entity name
                const currentPovEntity = Chat.getCurrentPovEntity();
                if (currentPovEntity) {
                    const $povTile = $(`.tile[data-coords-id="${currentPovEntity}"]`);
                    const $nameplate = $povTile.find('.nameplate');
                    if ($nameplate.length) {
                        displayName = $nameplate.text();
                    } else {
                        displayName = 'You';
                    }
                } else {
                    displayName = 'You';
                }
            } else if (sender === 'entity') {
            const speakerEntityId = options.entity_id || options.target_id || '';
            const currentPovEntity = Chat.getCurrentPovEntity();
            if (speakerEntityId && currentPovEntity && speakerEntityId === currentPovEntity) {
                const $povTile = $(`.tile[data-coords-id="${currentPovEntity}"]`);
                const $nameplate = $povTile.find('.nameplate');
                displayName = $nameplate.length ? $nameplate.text() : 'You';
            } else {
                const dialogEntityId = $('#dialogEntityName').data('entity-id');
                const lookupId = speakerEntityId || dialogEntityId;
                const $entityTile = lookupId ? $(`.tile[data-coords-id="${lookupId}"]`) : $();
                const $nameplate = $entityTile.find('.nameplate');
                if ($nameplate.length) {
                    displayName = $nameplate.text();
                } else {
                    displayName = 'Entity';
                }
            }
        }
        }

        const insightCheckHtml = options.insight_check
            ? Chat.renderInsightCheckBadge(options.insight_check)
            : '';
        const perceptionCheckHtml = options.perception_check
            ? Chat.renderPerceptionCheckBadge(options.perception_check)
            : '';
        const skillCheckRequestHtml = options.skill_check_request
            ? Chat.renderSkillCheckRequestBadge(options.skill_check_request)
            : '';
        const skillCheckResultHtml = options.skill_check_result
            ? Chat.renderSkillCheckResultBadge(options.skill_check_result)
            : '';
        const ttsHtml = Chat.renderTtsReplayButton(options.audio_url, {
            npcName: displayName,
            npcUid: options.entity_id || '',
            emotion: options.tts_emotion || 'calm',
        }, {
            replyId: options.reply_id || null,
            ttsStatus: options.tts_status || (options.audio_url ? 'ready' : ''),
            ttsText: options.message || content || '',
            ttsGain: options.tts_gain,
            ttsSkipped: options.tts_skipped,
        });

        const targetId = options.target_id || options.entity_id || '';
        const messageKey = options.message_key
            || (type === 'entity' && targetId && content
                ? Chat.dialogInsightTrackingKey(targetId, content)
                : '');
        const messageAttrs = [];
        if (messageKey) {
            messageAttrs.push(`data-message-key="${Chat.escapeHtml(messageKey)}"`);
        }
        if (targetId) {
            messageAttrs.push(`data-target-id="${Chat.escapeHtml(String(targetId))}"`);
        }
        if (options.insight_checked || options.insight_check) {
            messageAttrs.push('data-insight-checked="true"');
            if (messageKey) {
                Chat.markDialogInsightChecked(messageKey);
            }
        }
        if (options.reply_id) {
            messageAttrs.push(`data-reply-id="${Chat.escapeHtml(String(options.reply_id))}"`);
        }
        if (options.skill_checked || options.skill_check_result) {
            messageAttrs.push('data-skill-checked="true"');
        }

        const spokenHtml = useSegments
            ? segmentHtml
            : (content && String(content).trim()
                ? `<div class="message-content">${Chat.escapeHtml(content)}</div>`
                : '');

        const messageHtml = `
      <div class="dialog-chat-message ${type}" ${messageAttrs.join(' ')}>
        <div class="message-sender">${Chat.escapeHtml(displayName)}${ttsHtml}</div>
        ${spokenHtml}
        ${actionHtml}
        ${narrativeHtml}
        ${skillCheckRequestHtml}
        ${skillCheckResultHtml}
        ${insightCheckHtml}
        ${perceptionCheckHtml}
        <div class="message-timestamp">${timestamp}</div>
      </div>
    `;

        $('#dialogChatMessages').append(messageHtml);
        Chat.bindTtsReplayButtons($('#dialogChatMessages'));
        Chat.maybeScheduleTtsReplyPoll({
            reply_id: options.reply_id || null,
            tts_status: options.tts_status || (options.audio_url ? 'ready' : ''),
            tts_skipped: options.tts_skipped,
        });
        Chat.bindSkillCheckButtons($('#dialogChatMessages'));
        if (type === 'entity') {
            Chat.refreshDialogInsightButton();
        }

        // Scroll to bottom
        const $messages = $('#dialogChatMessages');
        $messages.scrollTop($messages[0].scrollHeight);
    },

    init: function () {
        Chat.initLocalConversationPanel();
        let aiInitialized = false;

        function applyAssistantReady(info) {
            try {
                aiInitialized = true;
                const label = info.current_model || info.provider_type || "ready";
                $("#chat-input, #send-chat, #draggable-chat-input, #draggable-send-chat").prop(
                    "disabled",
                    false
                );
                Chat.loadAssistantChatHistory(label);
            } catch (err) {
                console.error("[Chat] applyAssistantReady failed:", err);
                showAssistantUnavailable("Assistant UI failed to update. Check the browser console.");
            }
        }

        function showAssistantUnavailable(msg) {
            const m =
                msg ||
                "No LLM provider is available. Set LLM_PROVIDER and required keys in the server environment, then restart the app or reload the page.";
            const block =
                '<div class="chat-message system"><strong>AI Assistant:</strong> ' + Chat.escapeHtml(m) + "</div>";
            if ($("#chat-messages").length) {
                $("#chat-messages").html(block);
            }
            if ($("#draggable-chat-messages").length) {
                $("#draggable-chat-messages").html(block);
            }
            $("#chat-input, #send-chat, #draggable-chat-input, #draggable-send-chat").prop("disabled", true);
        }

        function syncAssistantFromServer() {
            const baseAjax = { dataType: "json", timeout: 45000 };

            $.ajax(
                $.extend({}, baseAjax, {
                    type: "GET",
                    url: "/ai/provider-info",
                    success: function (data) {
                        if (data && data.success && data.info && data.info.initialized === true) {
                            applyAssistantReady(data.info);
                            return;
                        }
                        $.ajax(
                            $.extend({}, baseAjax, {
                                type: "POST",
                                url: "/ai/initialize-from-env",
                                success: function (again) {
                                    if (
                                        again &&
                                        again.success &&
                                        again.info &&
                                        again.info.initialized === true
                                    ) {
                                        applyAssistantReady(again.info);
                                    } else {
                                        showAssistantUnavailable();
                                    }
                                },
                                error: function (_xhr, status) {
                                    const detail =
                                        status === "timeout"
                                            ? "Timed out contacting the server LLM. Check Ollama/llama.cpp is running or increase timeouts."
                                            : "Could not sync LLM from server configuration.";
                                    showAssistantUnavailable(detail);
                                },
                            })
                        );
                    },
                    error: function (_xhr, status) {
                        if (status === "timeout") {
                            showAssistantUnavailable(
                                "Timed out loading provider info. Check your connection and reload."
                            );
                        } else {
                            showAssistantUnavailable(
                                "Could not read LLM status (DM session required for the AI assistant)."
                            );
                        }
                    },
                })
            );
        }

        const roleLabel = String($("body").data("role") || "");
        if (roleLabel.indexOf("dm") !== -1) {
            syncAssistantFromServer();
        }

        // Handle chat form submission
        $("#chat-form, #draggable-chat-form").on("submit", function (e) {
            e.preventDefault();
            if (!aiInitialized) {
                const isDraggable = $(this).attr("id") === "draggable-chat-form";
                Chat.addChatMessage(
                    "system",
                    "AI assistant is not ready yet. Wait for the server LLM to sync, or check LLM_PROVIDER / keys and reload.",
                    isDraggable
                );
                return;
            }

            const isDraggable = $(this).attr("id") === "draggable-chat-form";
            const $input = isDraggable ? $("#draggable-chat-input") : $("#chat-input");
            const $sendButton = isDraggable ? $("#draggable-send-chat") : $("#send-chat");

            const message = $input.val().trim();
            if (message === "") return;

            // Add user message to chat
            Chat.addChatMessage("user", message, isDraggable);
            $input.val("");

            // Disable input while processing
            $input.prop("disabled", true);
            $sendButton.prop("disabled", true);

            // Add processing indicator
            const processingId = Chat.addProcessingMessage(isDraggable);

            // Set up timeout indicators for longer processing times
            const timeout1 = setTimeout(() => {
                Chat.updateProcessingMessage(processingId, "Processing your request", isDraggable);
            }, 3000);

            const timeout2 = setTimeout(() => {
                Chat.updateProcessingMessage(processingId, "Gathering game data", isDraggable);
            }, 8000);

            const timeout3 = setTimeout(() => {
                Chat.updateProcessingMessage(processingId, "Almost done", isDraggable);
            }, 15000);

            // Send message to AI
            $.ajax({
                type: "POST",
                url: "/ai/chat",
                data: { message: message },
                success: (data) => {
                    // Clear timeouts
                    clearTimeout(timeout1);
                    clearTimeout(timeout2);
                    clearTimeout(timeout3);

                    // Remove processing indicator
                    Chat.removeProcessingMessage(processingId, isDraggable);

                    if (data.success) {
                        Chat.addChatMessage("assistant", data.response, isDraggable);
                    } else {
                        Chat.addChatMessage("system", "Error: " + (data.error || "Unknown error"), isDraggable);
                    }
                    $input.prop("disabled", false);
                    $sendButton.prop("disabled", false);
                    $input.focus();
                },
                error: () => {
                    // Clear timeouts
                    clearTimeout(timeout1);
                    clearTimeout(timeout2);
                    clearTimeout(timeout3);

                    // Remove processing indicator
                    Chat.removeProcessingMessage(processingId, isDraggable);

                    Chat.addChatMessage("system", "Network error occurred while communicating with AI.", isDraggable);
                    $input.prop("disabled", false);
                    $sendButton.prop("disabled", false);
                    $input.focus();
                }
            });
        });

        // Clear chat history
        $("#clear-chat, #draggable-clear-chat").on("click", function () {
            if (confirm("Are you sure you want to clear the chat history?")) {
                const cleared =
                    '<div class="chat-message system"><strong>AI Assistant:</strong> Chat history cleared. How can I help you?</div>';
                Chat.assistantChatTargets().forEach(function (isDraggable) {
                    const $chatContainer = isDraggable ? $("#draggable-chat-messages") : $("#chat-messages");
                    $chatContainer.html(cleared);
                });

                // Clear server-side history
                $.ajax({
                    type: "POST",
                    url: "/ai/clear-history",
                    success: (data) => {
                        console.log("Chat history cleared");
                    }
                });
            }
        });

        // Get game context
        $("#get-context, #draggable-get-context").on("click", function () {
            const isDraggable = $(this).attr("id") === "draggable-get-context";
            $.ajax({
                type: "GET",
                url: "/ai/context",
                success: (data) => {
                    if (data.success) {
                        Chat.addChatMessage("system", "Game context retrieved and sent to AI assistant.", isDraggable);
                    } else {
                        Chat.addChatMessage("system", "Failed to get game context: " + (data.error || "Unknown error"), isDraggable);
                    }
                },
                error: () => {
                    Chat.addChatMessage("system", "Failed to get game context: Network error", isDraggable);
                }
            });
        });

        // AI Chat Panel — drag/resize using fixed left/top.
        // Document mousemove/mouseup are registered only while dragging/resizing (same idea as
        // initLocalConversationDrag), so we avoid running jQuery handlers on every pointer move.
        const AI_CHAT_LAYOUT_KEY = "aiChatPanelLayout_v2";
        const AI_CHAT_INTERACT_NS = "aiChatPanelInteract";
        let aiChatInteractState = null;

        function normalizeAiChatPanelPx($panel) {
            if (!$panel.length) {
                return;
            }
            const el = $panel[0];
            const rect = el.getBoundingClientRect();
            $panel.css({
                left: Math.round(rect.left) + "px",
                top: Math.round(rect.top) + "px",
                right: "auto",
                bottom: "auto",
                transform: "none",
            });
        }

        function saveAiChatPanelLayout() {
            const $panel = $("#ai-chat-panel");
            if (!$panel.length) {
                return;
            }
            try {
                localStorage.setItem(
                    AI_CHAT_LAYOUT_KEY,
                    JSON.stringify({
                        left: parseInt($panel.css("left"), 10) || 0,
                        top: parseInt($panel.css("top"), 10) || 0,
                        width: $panel.outerWidth(),
                        height: $panel.outerHeight(),
                    })
                );
            } catch (e) {
                console.warn("[Chat] saveAiChatPanelLayout failed", e);
            }
        }

        function loadAiChatPanelLayout() {
            const $panel = $("#ai-chat-panel");
            if (!$panel.length) {
                return;
            }
            const raw = localStorage.getItem(AI_CHAT_LAYOUT_KEY);
            if (!raw) {
                return;
            }
            try {
                const layout = JSON.parse(raw);
                if (layout.width) {
                    $panel.css("width", layout.width + "px");
                }
                if (layout.height) {
                    $panel.css("height", layout.height + "px");
                }
                if (typeof layout.left === "number") {
                    $panel.css("left", layout.left + "px");
                }
                if (typeof layout.top === "number") {
                    $panel.css("top", layout.top + "px");
                }
                $panel.css({ right: "auto", bottom: "auto", transform: "none" });
            } catch (e) {
                console.warn("[Chat] loadAiChatPanelLayout failed", e);
            }
        }

        function detachAiChatInteractListeners() {
            $(document).off("mousemove." + AI_CHAT_INTERACT_NS + " mouseup." + AI_CHAT_INTERACT_NS);
        }

        function endAiChatInteract($panel) {
            detachAiChatInteractListeners();
            aiChatInteractState = null;
            if ($panel && $panel.length) {
                $panel.removeClass("dragging").css("cursor", "");
            }
            saveAiChatPanelLayout();
        }

        $("#toggle-ai-chat").on("click", function () {
            const $panel = $("#ai-chat-panel");
            if ($panel.is(":visible")) {
                $panel.hide();
                $(this).removeClass("btn-success").addClass("btn-primary");
            } else {
                normalizeAiChatPanelPx($panel);
                $panel.show();
                $(this).removeClass("btn-primary").addClass("btn-success");
            }
        });

        $("#close-chat").on("click", function () {
            $("#ai-chat-panel").hide();
            $("#toggle-ai-chat").removeClass("btn-success").addClass("btn-primary");
        });

        $("#minimize-chat").on("click", function () {
            const $panel = $("#ai-chat-panel");
            const $body = $panel.find(".panel-body");

            if ($panel.hasClass("minimized")) {
                $panel.removeClass("minimized");
                $body.show();
                $(this).find("i").removeClass("glyphicon-plus").addClass("glyphicon-minus");
            } else {
                $panel.addClass("minimized");
                $body.hide();
                $(this).find("i").removeClass("glyphicon-minus").addClass("glyphicon-plus");
            }
        });

        $("#ai-chat-panel .panel-header").on("mousedown", function (e) {
            if (e.target.tagName === "BUTTON" || e.target.tagName === "I" || $(e.target).closest("button").length) {
                return;
            }
            const $panel = $("#ai-chat-panel");
            if (!$panel.length) {
                return;
            }
            detachAiChatInteractListeners();
            normalizeAiChatPanelPx($panel);

            const panelW = $panel.outerWidth();
            const panelH = $panel.outerHeight();
            aiChatInteractState = {
                mode: "drag",
                startX: e.clientX,
                startY: e.clientY,
                startLeft: parseInt($panel.css("left"), 10) || 0,
                startTop: parseInt($panel.css("top"), 10) || 0,
                panelW,
                panelH,
            };

            $panel.addClass("dragging").css("cursor", "grabbing");
            e.preventDefault();

            $(document)
                .on("mousemove." + AI_CHAT_INTERACT_NS, function (moveEv) {
                    if (!aiChatInteractState || aiChatInteractState.mode !== "drag") {
                        return;
                    }
                    moveEv.preventDefault();
                    const st = aiChatInteractState;
                    const dx = moveEv.clientX - st.startX;
                    const dy = moveEv.clientY - st.startY;
                    let nl = st.startLeft + dx;
                    let nt = st.startTop + dy;
                    const vw = window.innerWidth;
                    const vh = window.innerHeight;
                    nl = Math.max(0, Math.min(nl, vw - st.panelW));
                    nt = Math.max(0, Math.min(nt, vh - st.panelH));
                    $panel.css({ left: nl + "px", top: nt + "px" });
                })
                .on("mouseup." + AI_CHAT_INTERACT_NS, function () {
                    endAiChatInteract($panel);
                });
        });

        $("#ai-chat-panel .resize-handle").on("mousedown", function (e) {
            const $panel = $("#ai-chat-panel");
            if (!$panel.length) {
                return;
            }
            detachAiChatInteractListeners();
            normalizeAiChatPanelPx($panel);
            e.preventDefault();
            e.stopPropagation();

            const left = parseInt($panel.css("left"), 10) || 0;
            const top = parseInt($panel.css("top"), 10) || 0;
            aiChatInteractState = {
                mode: "resize",
                startX: e.clientX,
                startY: e.clientY,
                w0: $panel.outerWidth(),
                h0: $panel.outerHeight(),
                left,
                top,
            };

            $(document)
                .on("mousemove." + AI_CHAT_INTERACT_NS, function (moveEv) {
                    if (!aiChatInteractState || aiChatInteractState.mode !== "resize") {
                        return;
                    }
                    moveEv.preventDefault();
                    const st = aiChatInteractState;
                    const dx = moveEv.clientX - st.startX;
                    const dy = moveEv.clientY - st.startY;
                    let nw = Math.max(300, st.w0 + dx);
                    let nh = Math.max(400, st.h0 + dy);
                    const vw = window.innerWidth;
                    const vh = window.innerHeight;
                    nw = Math.min(nw, vw - st.left);
                    nh = Math.min(nh, vh - st.top);
                    $panel.css({ width: nw + "px", height: nh + "px" });
                })
                .on("mouseup." + AI_CHAT_INTERACT_NS, function () {
                    endAiChatInteract($panel);
                });
        });

        $(window).on("resize.aiChatPanel", function () {
            const $panel = $("#ai-chat-panel");
            if (!$panel.length || !$panel.is(":visible")) {
                return;
            }
            normalizeAiChatPanelPx($panel);
            const rect = $panel[0].getBoundingClientRect();
            const ww = $(window).width();
            const wh = $(window).height();
            let nl = rect.left;
            let nt = rect.top;
            if (rect.right > ww) {
                nl = ww - rect.width - 20;
            }
            if (rect.left < 0) {
                nl = 20;
            }
            if (rect.bottom > wh) {
                nt = wh - rect.height - 20;
            }
            if (rect.top < 0) {
                nt = 20;
            }
            $panel.css({ left: nl + "px", top: nt + "px", right: "auto", transform: "none" });
            saveAiChatPanelLayout();
        });

        $("#copy-chat-transcript").on("click", function () {
            Chat.copyTranscript(false);
        });
        $("#select-chat-transcript").on("click", function () {
            Chat.selectTranscript(false);
        });
        $("#draggable-copy-chat-transcript").on("click", function () {
            Chat.copyTranscript(true);
        });
        $("#draggable-select-chat-transcript").on("click", function () {
            Chat.selectTranscript(true);
        });
        loadAiChatPanelLayout();
        Chat.bindDialogInsightButtons($('#dialogChatMessages'));
        Chat.bindTtsReplayButtons($('#dialogChatMessages'));

    }
});