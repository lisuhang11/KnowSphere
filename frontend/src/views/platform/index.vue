<template>
  <div class="main">
    <Menu />
    <div class="platform-route-outlet">
      <RouterView />
    </div>
  </div>
</template>

<script setup lang="ts">
import Menu from '@/components/menu.vue'
</script>

<style lang="less">
.main {
  display: flex;
  align-items: stretch;
  width: 100%;
  height: 100%;
  min-width: 600px;
  min-height: 0;
  background: var(--td-bg-color-container);
}

.platform-route-outlet {
  flex: 1;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

img {
  -webkit-user-drag: none;
  user-select: none;
}
</style>
