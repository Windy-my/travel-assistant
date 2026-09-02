"""数据 providers：外部数据 API（天气 / POI / 路线）与演示数据源。

分层约定：
- demo          内置 curated 数据（北京/上海/成都/西安），完全离线可跑，结果最完整；
- openstreetmap 免费真实数据（Open-Meteo 地理编码 + Overpass POI + OSRM 路线），
                 富信息（评分/门票/简介）缺失时置空并标注估算；
- amap          高德地图 Web 服务（需 AMAP_KEY），POI/酒店/驾车/步行/公交全链路。
各获取函数返回 (data, provider_name, warnings)。
"""
