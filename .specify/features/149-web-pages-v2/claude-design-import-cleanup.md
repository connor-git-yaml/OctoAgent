# F149 Claude Design Spotify Import Mechanical Cleanup

只对当前 `OctoAgent Web.dc.html` 做机械 import 清理，不开启新设计 Round，不调整任何布局、颜色、间距、组件、文案、状态或行为。

在 `<head>` 中删除以下 5 个 Spotify Design System 引用：

1. `_ds/spotify-design-system-455229af-89f8-4bce-ade7-20822c2f87ef/tokens/fonts.css`
2. `_ds/spotify-design-system-455229af-89f8-4bce-ade7-20822c2f87ef/tokens/colors.css`
3. `_ds/spotify-design-system-455229af-89f8-4bce-ade7-20822c2f87ef/tokens/typography.css`
4. `_ds/spotify-design-system-455229af-89f8-4bce-ade7-20822c2f87ef/styles.css`
5. `_ds/spotify-design-system-455229af-89f8-4bce-ade7-20822c2f87ef/_ds_bundle.js`

把文件内 `font-family:'Figtree',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif` 改为 `font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif`，避免依赖 Spotify typography；不要改其他 CSS。

必须保持：

- 旧 page 1–3 不变；
- anchor 4 的 20 frame、Shared-States、Advanced-Pattern、References、20 行验收表、10×7 状态矩阵不变；
- 刚完成的 visual-only polish 不变；
- Design System selector 继续为空，不重新选择任何 Design System。

完成后只报告：`Spotify _ds path count=0`、`Figtree count=0`、`frame names=20`。任何一项无法做到时输出 `BLOCKED`。
