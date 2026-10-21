<script lang="ts">
  import {onMount} from 'svelte';
  import {defineCustomElements, type CollaborationElement} from '@freecollab/components/elements';
  import type {CollaborationClient} from '@freecollab/components';
  export let client: CollaborationClient;
  let chat: CollaborationElement;
  onMount(() => { defineCustomElements(); chat.client = client; return () => chat.destroy(); });
</script>
<freecollab-chat bind:this={chat}></freecollab-chat>
