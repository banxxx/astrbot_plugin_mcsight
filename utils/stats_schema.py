"""Shared six-category public Minecraft statistics schema; no imports or I/O."""

ACT, FLOW, NOISE = 'act', 'flow', 'noise'

CATEGORIES = {
    '探索': {
        'items': [
            ('步行距离', 'walkDistance', 'm', 3_000_000, FLOW),
            ('疾跑距离', 'sprintDistance', 'm', 2_000_000, FLOW),
            ('游泳距离', 'swimDistance', 'm', 200_000, FLOW),
            ('攀爬距离', 'climbDistance', 'm', 150_000, FLOW),
            ('骑马距离', 'horseDistance', 'm', 800_000, FLOW),
            ('划船距离', 'boatDistance', 'm', 500_000, FLOW),
            ('矿车距离', 'minecartDistance', 'm', 300_000, FLOW),
            ('鞘翅滑行', 'elytraDistance', 'm', 2_000_000, FLOW)
        ]
    },
    '战斗': {
        'items': [
            ('造成伤害', 'damageDealt', '点', 3_000_000, FLOW),
            ('承受伤害', 'damageTaken', '点', 1_000_000, FLOW),
            ('盾牌格挡', 'damageBlockedByShield', '点', 200_000, FLOW),
            ('吸收伤害', 'damageAbsorbed', '点', 300_000, FLOW),
            ('击杀生物', 'mobKills', '', 80_000, ACT),
            ('击杀玩家', 'playerKills', '', 300, ACT),
            ('死亡次数', 'deaths', '', 1_500, NOISE),
            ('击中标靶', 'targetHit', '', 20_000, ACT)
        ]
    },
    '工艺': {
        'items': [
            ('使用工作台', 'interactWithCraftingTable', '', 150_000, ACT),
            ('使用切石机', 'interactWithStonecutter', '', 40_000, ACT),
            ('使用熔炉', 'interactWithFurnace', '', 200_000, ACT),
            ('使用高炉', 'interactWithBlastFurnace', '', 80_000, ACT),
            ('使用烟熏炉', 'interactWithSmoker', '', 80_000, ACT),
            ('使用酿造台', 'interactWithBrewingStand', '', 80_000, ACT),
            ('使用铁砧', 'interactWithAnvil', '', 40_000, ACT),
            ('使用砂轮', 'interactWithGrindstone', '', 40_000, ACT),
            ('使用锻造台', 'interactWithSmithingTable', '', 20_000, ACT),
            ('物品附魔', 'enchantItem', '', 15_000, ACT)
        ]
    },
    '仓储': {
        'items': [
            ('打开箱子', 'openChest', '', 300_000, ACT),
            ('打开木桶', 'openBarrel', '', 200_000, ACT),
            ('打开末影箱', 'openEnderchest', '', 100_000, ACT),
            ('打开潜影盒', 'openShulkerBox', '', 100_000, ACT),
            ('查看漏斗', 'inspectHopper', '', 150_000, ACT),
            ('查看投掷器', 'inspectDropper', '', 50_000, ACT),
            ('查看发射器', 'inspectDispenser', '', 50_000, ACT),
            ('触发陷阱箱', 'triggerTrappedChest', '', 20_000, ACT)
        ]
    },
    '田园': {
        'items': [
            ('与村民对话', 'talkedToVillager', '', 30_000, ACT),
            ('与村民交易', 'tradedWithVillager', '', 30_000, ACT),
            ('繁殖动物', 'animalsBred', '', 30_000, ACT),
            ('钓鱼次数', 'fishCaught', '', 15_000, ACT),
            ('床上入眠', 'sleepInBed', '', 3_000, ACT),
            ('使用营火', 'interactWithCampfire', '', 30_000, ACT),
            ('吃蛋糕', 'eatCakeSlice', '', 2_000, ACT),
            ('袭击胜利', 'raidWin', '', 250, ACT)
        ]
    },
    '休闲': {
        'items': [
            ('播放唱片', 'playRecord', '', 1_000, ACT),
            ('播放音符盒', 'playNoteblock', '', 15_000, ACT),
            ('音符盒调音', 'tuneNoteblock', '', 40_000, ACT),
            ('鸣钟', 'bellRing', '', 3_000, ACT),
            ('使用织布机', 'interactWithLoom', '', 15_000, ACT),
            ('使用制图台', 'interactWithCartographyTable', '', 15_000, ACT),
            ('花盆种花', 'potFlower', '', 3_000, ACT),
            ('填充炼药锅', 'fillCauldron', '', 5_000, ACT)
        ]
    }
}

