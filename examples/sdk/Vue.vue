<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref } from 'vue';
import { defineCustomElements, type CollaborationElement } from '@freecollab/components/elements';
import type { CollaborationClient } from '@freecollab/components';
const props = defineProps<{client: CollaborationClient}>();
const chat = ref<CollaborationElement>();
defineCustomElements();
onMounted(() => { if (chat.value) { chat.value.options = {locale: 'sv'}; chat.value.client = props.client; } });
onBeforeUnmount(() => chat.value?.destroy());
// Configure Vue compilerOptions.isCustomElement = tag => tag.startsWith('freecollab-').
</script>
<template><freecollab-chat ref="chat" /></template>
